"""
alarm_manager.py — Alarm acknowledgement and ack-timeout management.

AlarmManager belongs to one DeviceHandler and manages:
  - DEV-31: remote alarm acknowledgement (Presence On → Ack)
  - Ack-timeout: re-raising silenced alarms after ACK_TIMEOUT_SEC seconds
  - Ack-timestamp tracking (set / clear / expired query)

Fix 16 (Ziel 2): every SetAlertState transaction evaluates the final
InvocationState of the OperationResult.  sdc11073 resolves the Future with a
result (never an exception) for Fail / Cnclld / CnclldMan, so the result MUST
be read — before Fix 16 a provider Fail was logged as a success.

    outcome      InvocationState / cause
    ---------    ---------------------------------------------------------
    accepted     Fin, FinMod (FinMod: the provider adjusted the proposed
                 state; counted as executed, logged at WARNING, the episodic
                 report decides whether Presence really became Ack)
    failed       Fail, Cnclld, CnclldMan (provider refused / cancelled)
    timeout      SetResponse carried Wait/Start and no final
                 OperationInvokedReport arrived within ACK_WAIT_SEC
    error        transport error / exception (HTTP error, socket, …)
    unavailable  no consumer, MDIB or SetService — nothing was sent
"""

from __future__ import annotations

import concurrent.futures
import logging
from typing import TYPE_CHECKING, Any, Optional

from sdc11073.xml_types import pm_types
from sdc11073.xml_types import pm_qnames as pm
from sdc11073.xml_types.msg_types import InvocationState

if TYPE_CHECKING:
    from .handler import DeviceHandler


OUTCOME_ACCEPTED = 'accepted'
OUTCOME_FAILED = 'failed'
OUTCOME_TIMEOUT = 'timeout'
OUTCOME_ERROR = 'error'
OUTCOME_UNAVAILABLE = 'unavailable'

_SUCCESS_STATES = {InvocationState.FINISHED, InvocationState.FINISHED_MOD}


def classify_invocation_state(state: Any) -> str:
    """Map a final InvocationState onto the outcome vocabulary (Q1)."""
    try:
        value = InvocationState(str(getattr(state, 'value', state)))
    except Exception:
        return OUTCOME_FAILED
    return OUTCOME_ACCEPTED if value in _SUCCESS_STATES else OUTCOME_FAILED


class AlarmManager:
    """
    Handles alarm acknowledgement and ack-timeout tracking for a single device.

    Shares the DeviceHandler's data_lock and consumer references — all network
    calls follow the two-phase pattern (read under lock, send without lock).
    """

    ACK_TIMEOUT_SEC: float = 30.0
    # Wait for the final OperationInvokedReport.  The SetResponse itself is
    # synchronous; this bounds the wait for Wait/Start → final state (Q1).
    ACK_WAIT_SEC: float = 5.0

    def __init__(self, handler: 'DeviceHandler') -> None:
        self._handler = handler
        # Maps AlertSignal descriptor handle → monotonic time of Ack transition.
        # Populated by on_alert_update(); consumed by _process_ack_timeouts().
        self._ack_timestamps: dict[str, float] = {}

    # =========================================================================
    # Ack-timestamp management
    # =========================================================================

    def set_ack(self, handle: str, timestamp: float) -> None:
        """Record the time an alarm signal transitioned to Ack."""
        self._ack_timestamps[handle] = timestamp

    def clear_ack(self, handle: str) -> None:
        """Remove ack tracking for a signal (alarm cleared or re-raised)."""
        self._ack_timestamps.pop(handle, None)

    def is_tracked(self, handle: str) -> bool:
        """Return True if this signal's ack timer is currently running."""
        return handle in self._ack_timestamps

    def get_expired_handles(self, now: float) -> list[str]:
        """Return handles whose Ack has been held longer than ACK_TIMEOUT_SEC."""
        return [
            h for h, t in list(self._ack_timestamps.items())
            if now - t >= self.ACK_TIMEOUT_SEC
        ]

    # =========================================================================
    # SetAlertState transaction (shared by ack and re-raise) — Fix 16
    # =========================================================================

    def _set_alert_presence(
        self,
        operation_handle: str,
        alert_signal_handle: str,
        presence: Any,
        purpose: str,
        press_id: Optional[str] = None,
    ) -> tuple[str, Optional[str], str]:
        """
        Send SetAlertState(Presence=``presence``) and evaluate the final
        InvocationState.  Returns (outcome, invocation_state, message).

        TWO-PHASE PATTERN:
          Phase 1 (data_lock):  read MDIB, build proposed AlertSignalState.
          Phase 2 (no lock):    send SetAlertState over the network, wait for
                                the final OperationInvokedReport (ACK_WAIT_SEC).
        """
        handler = self._handler
        tag = '[Ack]' if purpose == 'ack' else '[Ack-timeout]'
        proposed_state = None

        # Phase 1: prepare proposed state
        try:
            with handler.data_lock:
                if not handler.consumer or not handler.mdib:
                    handler.logger.warning(f'{tag} consumer or mdib not available.')
                    return OUTCOME_UNAVAILABLE, None, 'consumer or mdib not available'
                if not handler.consumer.set_service_client:
                    handler.logger.warning(f'{tag} set_service_client not available.')
                    return OUTCOME_UNAVAILABLE, None, 'no SetService'
                proposed_state = handler.mdib.xtra.mk_proposed_state(alert_signal_handle)
                proposed_state.Presence = presence
        except Exception as e:
            handler.logger.error(f'{tag} failed to prepare SetAlertState: {e}')
            return OUTCOME_ERROR, None, f'prepare: {e}'

        # Phase 2: network call (no lock), timed as RTT_op (Ziel 2 metric)
        _lm = None
        _t0 = None
        try:
            from app.metrics.latency_metrics import get_latency_metrics
            _lm = get_latency_metrics()
            _t0 = _lm.ack_start(alert_signal_handle)
        except Exception:
            _lm = None

        outcome = OUTCOME_ERROR
        state_str: Optional[str] = None
        transaction_id: Optional[int] = None
        message = ''
        try:
            future = handler.consumer.set_service_client.set_alert_state(
                operation_handle, proposed_state
            )
            result = future.result(timeout=self.ACK_WAIT_SEC)
            info = getattr(result, 'InvocationInfo', None)
            state = getattr(info, 'InvocationState', None)
            transaction_id = getattr(info, 'TransactionId', None)
            state_str = None if state is None else str(getattr(state, 'value', state))
            outcome = classify_invocation_state(state)
            if outcome == OUTCOME_ACCEPTED:
                if state_str == InvocationState.FINISHED_MOD.value:
                    message = 'FinMod: provider modified the proposed state'
                    handler.logger.warning(
                        f"{tag} '{alert_signal_handle}' InvocationState=FinMod "
                        f'(transaction {transaction_id}) — {message}.'
                    )
                else:
                    handler.logger.info(
                        f"{tag} '{alert_signal_handle}' InvocationState={state_str} "
                        f'(transaction {transaction_id}) — Presence={presence} accepted.'
                    )
            else:
                err = getattr(info, 'InvocationError', None)
                err_msgs = getattr(info, 'InvocationErrorMessage', None) or []
                try:
                    err_text = ', '.join(str(getattr(m, 'text', m)) for m in err_msgs)
                except Exception:
                    err_text = str(err_msgs)
                message = f'{state_str} {getattr(err, "value", err) or ""} {err_text}'.strip()
                handler.logger.error(
                    f"{tag} '{alert_signal_handle}' REFUSED: InvocationState={state_str} "
                    f'(transaction {transaction_id}) {message}'
                )
        except concurrent.futures.TimeoutError:
            outcome = OUTCOME_TIMEOUT
            message = f'no final OperationInvokedReport within {self.ACK_WAIT_SEC:g} s'
            handler.logger.error(f"{tag} '{alert_signal_handle}' TIMEOUT: {message}.")
        except Exception as e:
            outcome = OUTCOME_ERROR
            message = f'{type(e).__name__}: {e}'
            handler.logger.error(f"{tag} '{alert_signal_handle}' ERROR: {message}")

        if _lm is not None and _t0 is not None:
            try:
                _lm.ack_stop(
                    alert_signal_handle, _t0, ok=(outcome == OUTCOME_ACCEPTED),
                    epr=getattr(handler, 'epr', None), operation_handle=operation_handle,
                    invocation_state=state_str, transaction_id=transaction_id,
                    press_id=press_id, outcome=outcome, purpose=purpose,
                )
            except Exception:
                pass
        return outcome, state_str, message

    # =========================================================================
    # DEV-31: Remote alarm acknowledgement
    # =========================================================================

    def acknowledge_alarm(self, operation_handle: str, alert_signal_handle: str,
                          press_id: Optional[str] = None) -> str:
        """
        Transition an active alarm signal from On → Ack on the device.

        Returns the outcome (accepted | failed | timeout | error | unavailable).
        ``press_id`` groups the transactions of one bell press (A4).
        """
        outcome, _state, _msg = self._set_alert_presence(
            operation_handle, alert_signal_handle,
            pm_types.AlertSignalPresence.ACK, 'ack', press_id=press_id,
        )
        return outcome

    def acknowledge_alarm_detailed(self, operation_handle: str, alert_signal_handle: str,
                                   press_id: Optional[str] = None) -> tuple[str, Optional[str], str]:
        """Like ``acknowledge_alarm`` but returns (outcome, invocation_state, message)
        for the UI (Q2)."""
        return self._set_alert_presence(
            operation_handle, alert_signal_handle,
            pm_types.AlertSignalPresence.ACK, 'ack', press_id=press_id,
        )

    # =========================================================================
    # Ack-timeout: re-raise silenced alarms
    # =========================================================================

    def reactivate_alarm(self, operation_handle: str, alert_signal_handle: str) -> str:
        """
        Re-raise an alarm that has been in Ack state beyond ACK_TIMEOUT_SEC.

        Sends SetAlertState(Presence=On) to the provider and evaluates the
        InvocationState exactly like the acknowledgement (A2).  Called via
        asyncio.to_thread() from the monitoring loop so the event loop is
        never blocked.  Returns the outcome.
        """
        outcome, _state, _msg = self._set_alert_presence(
            operation_handle, alert_signal_handle,
            pm_types.AlertSignalPresence.ON, 'reraise',
        )
        return outcome

    def find_operation_handle(self, sig_handle: str) -> str | None:
        """
        Search MDIB for a SetAlertStateOperationDescriptor targeting *sig_handle*.

        Returns the operation Handle, or None if not found.  Fix 16: no
        fallback to an unrelated operation — the provider rejects a wrong
        OperationTarget, and the re-raise must not touch another signal.
        Must NOT be called while holding data_lock (acquires it internally).
        """
        handler = self._handler
        op_handle = None
        try:
            with handler.data_lock:
                if handler.mdib:
                    op_descs = handler.mdib.descriptions.NODETYPE.get(
                        pm.SetAlertStateOperationDescriptor, []
                    )
                    for op in op_descs:
                        if op.OperationTarget == sig_handle:
                            op_handle = op.Handle
                            break
        except Exception as e:
            handler.logger.error(f'[Ack-timeout] Could not find op_handle: {e}')
        return op_handle
