"""
app.metrics — KPI recorders for Ziel 1–3 over a JSON Lines event journal.

    from app.metrics import get_topology_metrics, get_latency_metrics, get_arr_metrics
    from app.metrics import configure_event_log, get_event_log

The ten call sites in ``sdcMyConsumer``, ``device/handler``, ``device/alarm_manager``
and ``smartAlertAggregator`` import these lazily inside try/except, so the
package stays optional for headless tests.  The journal path is set once by
``configure_event_log(path)`` (``--event-log``); default ``logs/events.jsonl``.
"""
from .eventlog import EventLog, configure as configure_event_log, get_event_log
from .topology_metrics import TopologyMetrics, get_topology_metrics
from .latency_metrics import LatencyMetrics, get_latency_metrics
from .arr_metrics import ArrMetrics, get_arr_metrics

__all__ = [
    'EventLog', 'configure_event_log', 'get_event_log',
    'TopologyMetrics', 'get_topology_metrics',
    'LatencyMetrics', 'get_latency_metrics',
    'ArrMetrics', 'get_arr_metrics',
]
