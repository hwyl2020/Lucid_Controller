"""Bounded frame queues with the two policies from the spec.

- LatestFrameQueue (display): never blocks, newest frame wins, drops are counted.
- RecordingQueue: never drops silently; a full queue is reported, counted and logged.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections import deque

from app.acquisition.frame import Frame
from app.camera_log import for_camera

logger = logging.getLogger(__name__)


class LatestFrameQueue:
    def __init__(self, maxsize: int = 2) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be >= 1")
        self._frames: deque[Frame] = deque(maxlen=maxsize)
        self._lock = threading.Lock()
        self._dropped = 0

    def put(self, frame: Frame) -> None:
        """Add a frame, evicting the oldest one if full."""
        with self._lock:
            if len(self._frames) == self._frames.maxlen:
                self._dropped += 1
            self._frames.append(frame)

    def get_latest(self) -> Frame | None:
        """Return the newest frame (or None) and discard any older ones."""
        with self._lock:
            if not self._frames:
                return None
            frame = self._frames.pop()
            self._dropped += len(self._frames)
            self._frames.clear()
            return frame

    @property
    def dropped(self) -> int:
        with self._lock:
            return self._dropped

    def __len__(self) -> int:
        with self._lock:
            return len(self._frames)


class RecordingQueue:
    def __init__(self, maxsize: int = 256) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be >= 1")
        self._queue: queue.Queue[Frame] = queue.Queue(maxsize)
        self._lock = threading.Lock()
        self._overflow = 0

    def put(self, frame: Frame) -> bool:
        """Enqueue without blocking. Returns False (and records the overflow) if the queue is full."""
        try:
            self._queue.put_nowait(frame)
            return True
        except queue.Full:
            with self._lock:
                self._overflow += 1
                count = self._overflow
            if count == 1 or count % 100 == 0:
                logger.warning(
                    "Recording queue full for %s: %d frame(s) not recorded", frame.camera_id, count,
                    extra=for_camera(frame.camera_id),
                )
            return False

    def get(self, timeout: float | None = None) -> Frame | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    @property
    def overflow_count(self) -> int:
        with self._lock:
            return self._overflow

    @property
    def depth(self) -> int:
        return self._queue.qsize()

    @property
    def maxsize(self) -> int:
        return self._queue.maxsize
