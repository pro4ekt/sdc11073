"""
device/__init__.py — SDC Device Handler package.

Public API:
  DeviceHandler — worker thread that manages one SDC device connection.

Module-level side effects (applied once at first import):
  - Logging configured: 'sdc.consumer' logger with console + rotating file handlers.
  - HTTP 400 spam filters attached to sdc11073 internal loggers.
  - RelatedMeasurement.from_node() monkey-patched (deserialization bug fix).
"""

from .logging_setup import setup_module_logger, apply_sdc_log_filters
from .patches import apply_patches
from .handler import DeviceHandler

# Apply once at import time
_module_log = setup_module_logger()
apply_sdc_log_filters()
apply_patches()

__all__ = ['DeviceHandler']

