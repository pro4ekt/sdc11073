"""
logging_setup.py — Logging configuration for the SDC consumer worker.

Provides:
  - _SuppressGetContextStates400  — log filter that drops repetitive HTTP 400 spam
  - setup_module_logger()         — configure the 'sdc.consumer' logger
  - apply_sdc_log_filters()       — attach filters to sdc11073 internal loggers
"""

import logging
import logging.handlers
import pathlib

# Logs directory: Qt/logs/  (created automatically if absent)
_LOG_DIR = pathlib.Path(__file__).parent.parent / 'logs'
_LOG_DIR.mkdir(exist_ok=True)


class _SuppressGetContextStates400(logging.Filter):
    """
    Suppress repetitive 'GetContextStates HTTP 400' ERROR spam from sdc11073.

    sdcX returns HTTP 400 for GetContextStates when the consumer is not
    authorized (no mTLS). We handle this in the ping loop already and log
    a one-time warning — the sdc11073 internal ERROR is redundant and noisy.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno != logging.ERROR:
            return True
        msg = record.getMessage()
        if 'GetContextStates' in msg and '400' in msg:
            return False
        if 'HTTPReturnCodeError' in msg or '_get_context_states' in msg:
            return False
        return True


def setup_module_logger() -> logging.Logger:
    """
    Configure and return the root 'sdc.consumer' logger.

    Levels:
      Console (StreamHandler):         INFO  — short format  HH:MM:SS [LEVEL] msg
      File (RotatingFileHandler):      DEBUG — full format with logger name
        File:     Qt/logs/sdc_consumer.log
        Rotation: 5 MB × 5 backup copies

    NOTE: call this once at import time; re-imports are safe (handlers are
    not added twice because of the early-return guard).
    """
    logger = logging.getLogger('sdc.consumer')
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger

    # Console handler — INFO and above only (no DEBUG spam on the terminal)
    con_handler = logging.StreamHandler()
    con_handler.setLevel(logging.INFO)
    con_handler.setFormatter(logging.Formatter(
        fmt='%(asctime)s [%(levelname)-5s] %(message)s',
        datefmt='%H:%M:%S',
    ))
    logger.addHandler(con_handler)

    # File handler — full DEBUG, rotating 5 MB × 5
    file_handler = logging.handlers.RotatingFileHandler(
        filename=str(_LOG_DIR / 'sdc_consumer.log'),
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding='utf-8',
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        fmt='%(asctime)s [%(levelname)-8s] %(name)s -- %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    ))
    logger.addHandler(file_handler)

    logger.propagate = False
    return logger


def apply_sdc_log_filters() -> None:
    """
    Attach _SuppressGetContextStates400 to the sdc11073 internal loggers
    that produce the repetitive HTTP 400 error noise.

    Safe to call multiple times (each call attaches a new filter instance,
    but sdc11073's own log lines are deduplicated by the filter logic).
    """
    _f = _SuppressGetContextStates400()
    logging.getLogger('sdc.client.soap').addFilter(_f)
    logging.getLogger('sdc.client.mdib').addFilter(_f)

