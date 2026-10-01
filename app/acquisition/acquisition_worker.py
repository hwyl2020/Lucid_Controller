"""Per-camera acquisition thread: camera -> display queue (+ optional recording queue)."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from app.acquisition.frame import Frame
from app.acquisition.frame_queue import LatestFrameQueue, RecordingQueue
from app.cameras.camera_device import CameraDevice, CameraError, FrameTimeoutError
from app.models.units import bytes_per_second_to_mbps

logger = logging.getLogger(__name__)

FPS_WINDOW_S = 1.0


@dataclass(frozen=True)
class AcquisitionStats:
    frames_acquired: int = 0
    timeouts: int = 0
    measured_fps: float = 0.0
    bandwidth_mbps: float = 0.0  # image data delivered to the app, megabits per second
    frames_missed: int = 0  # frame-id gaps: frames the camera produced that never arrived
    last_frame_id: int | None = None
    error: str | None = None


class AcquisitionWorker:
    """Runs camera.start_acquisition() / get_frame() loop / stop_acquisition() on its own thread.

    ``on_error(camera_id, exc)`` is called from the worker thread when acquisition stops because
    of an error; keep it short and thread-safe.
    """

    def __init__(
        self,
        camera: CameraDevice,
        display_queue: LatestFrameQueue,
        *,
        frame_timeout: float = 1.0,
        on_error: Callable[[str, Exception], None] | None = None,
    ) -> None:
        self._camera = camera
        self._display_queue = display_queue
        self._frame_timeout = frame_timeout
        self._on_error = on_error
        self.recording_queue: RecordingQueue | None = None  # attach/detach at any time
        self.last_frame: Frame | None = None  # most recent frame, for snapshots
        self._stop_event = threading.Event()
        self._stats_lock = threading.Lock()
        self._stats = AcquisitionStats()
        self._thread = threading.Thread(
            target=self._run, name=f"acq-{camera.camera_id}", daemon=True
        )

    @property
    def camera_id(self) -> str:
        return self._camera.camera_id

    @property
    def running(self) -> bool:
        return self._thread.is_alive()

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> bool:
        """Signal the thread to stop and wait. Returns False if it did not finish in time."""
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout)
        if self._thread.is_alive():
            logger.warning("Acquisition thread for %s did not stop within %.1fs", self.camera_id, timeout)
            return False
        return True

    def stats(self) -> AcquisitionStats:
        with self._stats_lock:
            return self._stats

    def _run(self) -> None:
        camera = self._camera
        try:
            camera.start_acquisition()
        except Exception as exc:  # noqa: BLE001 - reported, never crashes the app
            self._fail(exc)
            return
        logger.info("Acquisition started for %s", camera.camera_id)

        window_start = time.perf_counter()
        window_frames = 0
        window_bytes = 0
        last_id: int | None = None
        try:
            while not self._stop_event.is_set():
                try:
                    frame = camera.get_frame(self._frame_timeout)
                except FrameTimeoutError:
                    with self._stats_lock:
                        self._stats = replace(self._stats, timeouts=self._stats.timeouts + 1)
                    continue

                self.last_frame = frame
                self._display_queue.put(frame)
                recording_queue = self.recording_queue
                if recording_queue is not None:
                    recording_queue.put(frame)

                missed = frame.frame_id - last_id - 1 if last_id is not None and frame.frame_id > last_id + 1 else 0
                last_id = frame.frame_id
                window_frames += 1
                window_bytes += frame.data.nbytes
                now = time.perf_counter()
                elapsed = now - window_start
                with self._stats_lock:
                    fps, bandwidth = self._stats.measured_fps, self._stats.bandwidth_mbps
                    if elapsed >= FPS_WINDOW_S:
                        fps = window_frames / elapsed
                        bandwidth = bytes_per_second_to_mbps(window_bytes / elapsed)
                        window_start, window_frames, window_bytes = now, 0, 0
                    self._stats = replace(
                        self._stats,
                        frames_acquired=self._stats.frames_acquired + 1,
                        frames_missed=self._stats.frames_missed + missed,
                        last_frame_id=frame.frame_id,
                        measured_fps=fps,
                        bandwidth_mbps=bandwidth,
                    )
        except Exception as exc:  # noqa: BLE001 - reported, never crashes the app
            self._fail(exc)
        finally:
            try:
                camera.stop_acquisition()
            except CameraError as exc:
                logger.debug("stop_acquisition for %s failed: %s", camera.camera_id, exc)
            logger.info("Acquisition stopped for %s", camera.camera_id)

    def _fail(self, exc: Exception) -> None:
        if isinstance(exc, CameraError):
            logger.error("Acquisition error on %s: %s", self.camera_id, exc)
        else:
            logger.exception("Unexpected acquisition failure on %s", self.camera_id)
        with self._stats_lock:
            self._stats = replace(self._stats, error=str(exc) or type(exc).__name__)
        if self._on_error is not None:
            self._on_error(self.camera_id, exc)
