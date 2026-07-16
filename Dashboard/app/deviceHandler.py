"""
app/deviceHandler.py — Backward-compatible re-export.

Delegates everything to the device/ package.
Also re-exports _module_log so that main.py can write session separators
to the same rotating log file that DeviceHandler uses.
"""

from device import DeviceHandler, _module_log  # noqa: F401

__all__ = ['DeviceHandler', '_module_log']
