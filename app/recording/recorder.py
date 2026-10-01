"""Per-camera recorder: drains a RecordingQueue on its own thread into a frame writer.

Raw mode (primary) appends frames losslessly to ``frames.raw`` with a ``frames.csv`` index, at full
resolution and native pixel format (e.g. raw BayerRG8). Measured on the dev PC: ~8,300 Mb/s sequential
write vs ~890 Mb/s from a 12 MP camera at 9 FPS. Use ``read_raw_sequence`` to read it back.
"""

from __future__ import annotations

import csv
import logging
import threading
from collections.abc import Iterator
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Protocol

import numpy as np

from app.acquisition.frame import Frame
from app.acquisition.frame_queue import RecordingQueue

logger = logging.getLogger(__name__)

RAW_DATA_FILE = "frames.raw"
INDEX_FILE = "frames.csv"
INDEX_FIELDS = ("frame_id", "timestamp", "offset", "nbytes", "width", "height", "pixel_format", "dtype", "channels")
WRITE_BUFFER_BYTES = 8 * 1024 * 1024


class RecordingMode(Enum):
    RAW = "raw"  # lossless native frames (frames.raw + frames.csv)
    VIDEO = "video"  # MP4; value kept as "video" for existing config files
    AVI = "avi"
    MOV = "mov"
    MKV = "mkv"

    @property
    def is_video(self) -> bool:
        return self is not RecordingMode.RAW

    @property
    def label(self) -> str:
        return {"raw": "Raw (lossless)", "video": "MP4", "avi": "AVI", "mov": "MOV", "mkv": "MKV"}[self.value]


class FrameWriter(Protocol):
    def write(self, frame: Frame) -> int: ...  # returns bytes written
    def close(self) -> None: ...


class RawSequenceWriter:
    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self._data = open(directory / RAW_DATA_FILE, "wb", buffering=WRITE_BUFFER_BYTES)
        self._index_file = open(directory / INDEX_FILE, "w", newline="", encoding="utf-8")
        self._index = csv.writer(self._index_file)
        self._index.writerow(INDEX_FIELDS)
        self._offset = 0

    def write(self, frame: Frame) -> int:
        data = np.ascontiguousarray(frame.data)
        self._data.write(memoryview(data).cast("B"))
        channels = 1 if data.ndim == 2 else data.shape[2]
        self._index.writerow((
            frame.frame_id, f"{frame.timestamp:.6f}", self._offset, data.nbytes,
            frame.width, frame.height, frame.pixel_format, data.dtype.str, channels,
        ))
        self._offset += data.nbytes
        return data.nbytes

    def close(self) -> None:
        self._data.close()
        self._index_file.close()


def read_raw_sequence(directory: Path) -> Iterator[Frame]:
    """Yield frames recorded by RawSequenceWriter (camera_id is the directory name)."""
    with open(directory / INDEX_FILE, newline="", encoding="utf-8") as index, open(directory / RAW_DATA_FILE, "rb") as data:
        for row in csv.DictReader(index):
            data.seek(int(row["offset"]))
            buffer = data.read(int(row["nbytes"]))
            height, width, channels = int(row["height"]), int(row["width"]), int(row["channels"])
            shape = (height, width) if channels == 1 else (height, width, channels)
            yield Frame(
                camera_id=directory.name,
                frame_id=int(row["frame_id"]),
                timestamp=float(row["timestamp"]),
                width=width,
                height=height,
                pixel_format=row["pixel_format"],
                data=np.frombuffer(buffer, dtype=np.dtype(row["dtype"])).reshape(shape),
            )


@dataclass(frozen=True)
class RecorderStats:
    frames_written: int = 0
    bytes_written: int = 0
    frame_gaps: int = 0  # frames missing from the sequence (camera/transport drops)
    queue_overflows: int = 0  # frames that reached the app but could not be queued
    queue_depth: int = 0
    error: str | None = None


class CameraRecorder:
    def __init__(self, camera_id: str, writer: FrameWriter, queue: RecordingQueue) -> None:
        self.camera_id = camera_id
        self._writer = writer
        self._queue = queue
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._stats = RecorderStats()
        self._last_frame_id: int | None = None
        self._thread = threading.Thread(target=self._run, name=f"rec-{camera_id}", daemon=True)

    @property
    def queue(self) -> RecordingQueue:
        return self._queue

    @property
    def running(self) -> bool:
        return self._thread.is_alive()

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 30.0) -> RecorderStats:
        """Stop after writing everything already queued, then close the files."""
        self._stop.set()
        self._thread.join(timeout)
        if self._thread.is_alive():
            logger.error("Recorder for %s did not finish within %.0fs", self.camera_id, timeout)
        return self.stats()

    def stats(self) -> RecorderStats:
        with self._lock:
            return replace(self._stats, queue_overflows=self._queue.overflow_count, queue_depth=self._queue.depth)

    def _run(self) -> None:
        try:
            while True:
                frame = self._queue.get(timeout=0.2)
                if frame is None:
                    if self._stop.is_set():
                        break
                    continue
                written = self._writer.write(frame)
                gaps = self._gap_before(frame.frame_id)
                with self._lock:
                    self._stats = replace(
                        self._stats,
                        frames_written=self._stats.frames_written + 1,
                        bytes_written=self._stats.bytes_written + written,
                        frame_gaps=self._stats.frame_gaps + gaps,
                    )
        except Exception as exc:  # noqa: BLE001 - disk full, permissions, encoder failure...
            logger.error("Recording failed for %s: %s", self.camera_id, exc)
            with self._lock:
                self._stats = replace(self._stats, error=str(exc) or type(exc).__name__)
        finally:
            try:
                self._writer.close()
            except Exception as exc:  # noqa: BLE001
                logger.error("Closing recording for %s failed: %s", self.camera_id, exc)

    def _gap_before(self, frame_id: int) -> int:
        last, self._last_frame_id = self._last_frame_id, frame_id
        if last is None or frame_id <= last:
            return 0  # first frame, or ids restarted after a stream restart
        return frame_id - last - 1
