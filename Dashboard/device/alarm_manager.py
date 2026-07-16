"""
alarm_manager.py — Alarm acknowledgement and ack-timeout management.

AlarmManager belongs to one DeviceHandler and manages:
  - DEV-31: remote alarm acknowledgement (Presence On → Ack)
  - Ack-timeout: re-raising silenced alarms after ACK_TIMEOUT_SEC seconds
  - Ack-timestamp tracking (set / clear / expired query)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sdc11073.xml_types import pm_types
from sdc11073.xml_types import pm_qnames as pm

if TYPE_CHECKING:
    from .handler import DeviceHandler


class AlarmManager:
    """
    Handles alarm acknowledgement and ack-timeout tracking for a single device.

    Shares the DeviceHandler's data_lock and consumer references — all network
    calls follow the two-phase pattern (read under lock, send without lock).
    """

    ACK_TIMEOUT_SEC: float = 30.0

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
    # DEV-31: Remote alarm acknowledgement
    # =========================================================================

    def acknowledge_alarm(self, operation_handle: str, alert_signal_handle: str) -> None:
        """
        Transition an active alarm signal from On → Ack on the device.

        TWO-PHASE PATTERN:
          Phase 1 (data_lock):  read MDIB, build proposed AlertSignalState.
          Phase 2 (no lock):    send SetAlertState over the network.
        """
        handler = self._handler
        proposed_state = None

        # Phase 1: prepare proposed state
        try:
            with handler.data_lock:
                if not handler.consumer or not handler.mdib:
                    handler.logger.warning('acknowledge_alarm: consumer or mdib not available.')
                    return
                if not handler.consumer.set_service_client:
                    handler.logger.warning('acknowledge_alarm: set_service_client not available.')
                    return
                proposed_state = handler.mdib.xtra.mk_proposed_state(alert_signal_handle)
                proposed_state.Presence = pm_types.AlertSignalPresence.ACK
        except Exception as e:
            handler.logger.error(f'Failed to prepare alarm acknowledgement: {e}')
            return

        # Phase 2: network call (no lock)
        try:
            future = handler.consumer.set_service_client.set_alert_state(
                operation_handle, proposed_state
            )
            future.result(timeout=5)
            handler.logger.info(f"Alarm '{alert_signal_handle}' acknowledged successfully.")
        except Exception as e:
            handler.logger.error(f'Failed to acknowledge alarm: {e}')

    # =========================================================================
    # Ack-timeout: re-raise silenced alarms
    # =========================================================================

    def reactivate_alarm(self, operation_handle: str, alert_signal_handle: str) -> None:
        """
        Re-raise an alarm that has been in Ack state beyond ACK_TIMEOUT_SEC.

        Sends SetAlertState(Presence=On) to the provider.
        Called via asyncio.to_thread() from the monitoring loop so the event
        loop is never blocked.

        TWO-PHASE PATTERN (same as acknowledge_alarm).
        """
        handler = self._handler
        try:
            with handler.data_lock:
                if not handler.consumer or not handler.mdib:
                    return
                proposed = handler.mdib.xtra.mk_proposed_state(alert_signal_handle)
                proposed.Presence = pm_types.AlertSignalPresence.ON

            if handler.consumer.set_service_client:
                future = handler.consumer.set_service_client.set_alert_state(
                    operation_handle, proposed
                )
                future.result(timeout=5)
                handler.logger.info(
                    f'[Ack-timeout] {alert_signal_handle}: successfully re-raised to On.'
                )
        except Exception as e:
            handler.logger.error(f'reactivate_alarm error: {e}')

    def find_operation_handle(self, sig_handle: str) -> str | None:
        """
        Search MDIB for a SetAlertStateOperationDescriptor targeting *sig_handle*.

        Returns the operation Handle, or None if not found.
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
                    # Fallback: use the first available operation
                    if not op_handle and op_descs:
                        op_handle = op_descs[0].Handle
        except Exception as e:
            handler.logger.error(f'[Ack-timeout] Could not find op_handle: {e}')
        return op_handle

