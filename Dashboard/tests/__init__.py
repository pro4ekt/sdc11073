
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
