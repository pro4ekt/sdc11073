"""
operationLogger.py — Thread-safe operative session documentation writer.

PURPOSE (OR mode only):
  During a surgical session (--mode=op), every SDC event — metric updates,
  alarm state changes, context applications — is appended to a structured
  plain-text file for post-operative review and thesis documentation.

FILE NAMING:
  OR_session_{FamilyName}_{GivenName}_{YYYYMMDD_HHMMSS}.txt
  Written to output_dir (default: current working directory).

THREAD SAFETY:
  All write operations are protected by a threading.Lock so callbacks from
  multiple DeviceHandler threads can call log() concurrently without corruption.
"""

import threading
from datetime import datetime
from pathlib import Path


class OperationLogger:
    """
    Writes a structured plain-text operative session report.

    Lifecycle:
      1. Instantiated in SdcMyConsumer._ensemble_formation_task() after UUID is generated.
      2. log_*() methods called from DeviceHandler callbacks (any thread).
      3. finalize() called when Manager stops — writes footer and returns file path.
    """

    def __init__(self, patient_ctx: dict, ensemble_uuid: str, output_dir: str = '.'):
        """
        Parameters:
          patient_ctx   — dict from SdcMyConsumer.get_patient_context_data()
          ensemble_uuid — UUID string of the formed ensemble
          output_dir    — directory where the report file will be written
        """
        self._lock = threading.Lock()

        # Build filename: safe for all OS (no spaces in path)
        family = patient_ctx.get('family_name', 'unknown').replace(' ', '_')
        given  = patient_ctx.get('given_name',  'patient').replace(' ', '_')
        ts     = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'OR_session_{family}_{given}_{ts}.txt'

        self._path = Path(output_dir) / filename
        self._write_header(patient_ctx, ensemble_uuid)
        print(f'[OperationLogger] Session log started: {self._path}')

    # =========================================================================
    # Header / Footer
    # =========================================================================

    def _write_header(self, ctx: dict, ensemble_uuid: str) -> None:
        conditions_str = ', '.join(ctx.get('conditions', [])) or 'None recorded'
        weight = ctx.get('weight_value')
        height = ctx.get('height_value')
        weight_str = f"{weight} {ctx.get('weight_unit', 'kg')}" if weight else 'N/A'
        height_str = f"{height} {ctx.get('height_unit', 'cm')}" if height else 'N/A'

        lines = [
            '=' * 64,
            '  OPERATIVE SESSION DOCUMENTATION',
            '  SDC Orchestrator  |  OR Mode  |  IHE SDPi-A / BICEPS',
            '=' * 64,
            f"Session Start : {datetime.now().isoformat(timespec='seconds')}",
            f"Patient       : {ctx.get('given_name', '')} {ctx.get('family_name', '')}".strip(),
            f"Date of Birth : {ctx.get('birth_date', 'N/A')}",
            f"Conditions    : {conditions_str}",
            f"Weight        : {weight_str}",
            f"Height        : {height_str}",
            f"Ensemble UUID : {ensemble_uuid}",
            '=' * 64,
            '',
        ]
        with open(self._path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')

    def finalize(self) -> str:
        """
        Appends the session footer and returns the absolute path to the report file.
        Should be called once when the Manager stops.
        """
        footer = (
            f'\n{"=" * 64}\n'
            f"Session End   : {datetime.now().isoformat(timespec='seconds')}\n"
            f"{'=' * 64}\n"
        )
        with self._lock:
            with open(self._path, 'a', encoding='utf-8') as f:
                f.write(footer)
        print(f'[OperationLogger] Session log finalised: {self._path}')
        return str(self._path.resolve())

    # =========================================================================
    # Core write primitive
    # =========================================================================

    def log(self, device_epr: str, event_type: str, data: str) -> None:
        """
        Thread-safe append of a single event line.

        Format:
          [ISO-timestamp] [EVENT_TYPE  ] [epr-suffix] data
        The EPR is truncated to the last 12 characters to keep lines readable.
        """
        ts   = datetime.now().isoformat(timespec='milliseconds')
        epr_short = device_epr[-12:] if len(device_epr) > 12 else device_epr
        line = f'[{ts}] [{event_type:<12s}] [{epr_short}] {data}\n'
        with self._lock:
            with open(self._path, 'a', encoding='utf-8') as f:
                f.write(line)

    # =========================================================================
    # Typed log helpers
    # =========================================================================

    def log_metric(self, epr: str, handle: str, value: str, alarm: str = 'Off') -> None:
        """Log a single metric value (called from on_metric_update, throttled)."""
        self.log(epr, 'METRIC', f'{handle} = {value}  alarm={alarm}')

    def log_alarm(self, epr: str, handle: str, presence: str) -> None:
        """Log an alarm state transition (called from on_alert_update, immediate)."""
        self.log(epr, 'ALARM', f'{handle} → {presence}')

    def log_context_applied(self, epr: str, context_type: str) -> None:
        """Log that a BICEPS context was successfully written to a device."""
        self.log(epr, 'CONTEXT', f'{context_type} applied to device')

    def log_device_event(self, epr: str, message: str) -> None:
        """Log a generic device lifecycle event (connect, disconnect, error)."""
        self.log(epr, 'DEVICE', message)

    def log_ensemble(self, message: str) -> None:
        """Log an ensemble-level event (formation, UUID assignment)."""
        self.log('ensemble', 'ENSEMBLE', message)

