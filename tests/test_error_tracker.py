"""Tests for the in-memory ERROR/CRITICAL counter."""

import logging
import time

from core.error_tracker import ErrorCounterHandler


def _record(level: int) -> logging.LogRecord:
    return logging.LogRecord(
        name="t", level=level, pathname=__file__, lineno=1,
        msg="x", args=None, exc_info=None,
    )


def test_counts_error_and_critical():
    h = ErrorCounterHandler()
    h.emit(_record(logging.ERROR))
    h.emit(_record(logging.CRITICAL))
    assert h.count() == 2


def test_ignores_info_and_warning():
    h = ErrorCounterHandler()
    h.emit(_record(logging.INFO))
    h.emit(_record(logging.WARNING))
    assert h.count() == 0


def test_drops_events_outside_window():
    h = ErrorCounterHandler(window_seconds=3600)
    # inject a stale timestamp directly (25h old)
    h._events.append(time.time() - 25 * 3600)
    h.emit(_record(logging.ERROR))  # fresh one
    assert h.count() == 1


def test_handler_logging_integration():
    h = ErrorCounterHandler()
    logger = logging.getLogger("error_tracker_test")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(h)
    try:
        logger.error("boom")
        logger.info("noise")
        assert h.count() == 1
    finally:
        logger.removeHandler(h)
