"""In-memory log for the UI log viewer: a logging.Handler with a bounded, thread-safe ring buffer.

Any thread may log; ``emit`` only appends under a lock (never touches the UI), so logging can never
block acquisition on rendering. The UI pulls new entries incrementally with ``since(seq)``.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass

DEFAULT_CAPACITY = 5000


@dataclass(frozen=True)
class LogEntry:
    seq: int  # monotonically increasing; survives clear()
    timestamp: float
    levelno: int
    level: str
    logger: str
    message: str


class LogBuffer(logging.Handler):
    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        super().__init__(level=logging.DEBUG)
        self._entries: deque[LogEntry] = deque(maxlen=capacity)
        self._lock_entries = threading.Lock()
        self._seq = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            if record.exc_info and record.exc_info[0] is not None:
                message += f" ({record.exc_info[0].__name__}: {record.exc_info[1]})"
            with self._lock_entries:
                self._seq += 1
                self._entries.append(
                    LogEntry(self._seq, record.created, record.levelno, record.levelname, record.name, message)
                )
        except Exception:  # noqa: BLE001 - logging must never raise into callers
            self.handleError(record)

    def since(self, seq: int) -> list[LogEntry]:
        """Entries with ``entry.seq > seq`` that are still in the buffer, oldest first."""
        with self._lock_entries:
            if not self._entries or self._entries[-1].seq <= seq:
                return []
            return [e for e in self._entries if e.seq > seq]

    def entries(self) -> list[LogEntry]:
        with self._lock_entries:
            return list(self._entries)

    def clear(self) -> None:
        with self._lock_entries:
            self._entries.clear()

    @property
    def last_seq(self) -> int:
        with self._lock_entries:
            return self._seq


def install(capacity: int = DEFAULT_CAPACITY) -> LogBuffer:
    """Attach a LogBuffer to the root logger (records still pass the root logger's level)."""
    buffer = LogBuffer(capacity)
    logging.getLogger().addHandler(buffer)
    return buffer
