"""In-memory ERROR/CRITICAL counter over a sliding time window.

A logging.Handler appends timestamps of ERROR+ records to a deque; reads
drop entries older than the window and return the remaining count. No log
parsing — independent of log format/rotation. Resets on bot restart (= deploy),
which is acceptable for a "something broke *now*" signal.
"""

import logging
import threading
import time
from collections import deque

_WINDOW_SECONDS = 24 * 60 * 60
_MAX_EVENTS = 10_000  # safety cap against an error storm leaking memory


class ErrorCounterHandler(logging.Handler):
    """Records timestamps of ERROR/CRITICAL log entries in a sliding window."""

    def __init__(self, window_seconds: int = _WINDOW_SECONDS) -> None:
        super().__init__(level=logging.ERROR)
        self._window = window_seconds
        self._events: deque[float] = deque(maxlen=_MAX_EVENTS)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        # emit may be called from chart_executor threads → guard with a lock.
        if record.levelno >= logging.ERROR:
            with self._lock:
                self._events.append(time.time())

    def count(self) -> int:
        cutoff = time.time() - self._window
        with self._lock:
            while self._events and self._events[0] < cutoff:
                self._events.popleft()
            return len(self._events)


_tracker = ErrorCounterHandler()


def get_tracker() -> ErrorCounterHandler:
    return _tracker


def get_error_count_24h() -> int:
    return _tracker.count()
