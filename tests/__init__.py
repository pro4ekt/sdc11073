# The Dashboard package uses the bare imports ``app.*`` / ``device.*``; put
# <repo>/Dashboard first on sys.path so the tests run from any working directory.
import sys as _sys
from pathlib import Path as _BootPath
_DASHBOARD_DIR = str(_BootPath(__file__).resolve().parents[1] / 'Dashboard')
if _DASHBOARD_DIR not in _sys.path:
    _sys.path.insert(0, _DASHBOARD_DIR)

# Keep the unit tests from writing into Dashboard/logs/events.jsonl: the real
# metric recorders are exercised by several suites, so the JSON Lines journal
# is redirected to a per-run temporary file (Fix 15).
try:
    import atexit as _atexit
    import tempfile as _tempfile
    from pathlib import Path as _Path
    from app.metrics.eventlog import configure as _configure_event_log, get_event_log as _get_event_log
    _tmp = _tempfile.TemporaryDirectory()
    _configure_event_log(_Path(_tmp.name) / 'events_tests.jsonl')
    _atexit.register(lambda: (_get_event_log().close(), _tmp.cleanup()))
except Exception:
    pass
