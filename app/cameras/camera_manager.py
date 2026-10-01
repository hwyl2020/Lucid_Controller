"""Owns the set of cameras and their acquisition workers. The UI talks to this, not to devices.

Methods are intended to be called from the UI/service thread. Expected camera failures surface as
CameraError (from connect etc.) or as CameraState.ERROR + last_error() (from acquisition).
"""

from __future__ import annotations

import logging
import threading

from app.acquisition.acquisition_worker import AcquisitionStats, AcquisitionWorker
from app.acquisition.frame import Frame
from app.acquisition.frame_queue import LatestFrameQueue, RecordingQueue
from app.cameras.camera_device import CameraDevice, CameraError
from app.models.camera_state import CameraState

logger = logging.getLogger(__name__)


class CameraManager:
    def __init__(self, display_queue_size: int = 2, frame_timeout: float = 1.0) -> None:
        self._display_queue_size = display_queue_size
        self._frame_timeout = frame_timeout
        self._lock = threading.RLock()
        self._cameras: dict[str, CameraDevice] = {}
        self._workers: dict[str, AcquisitionWorker] = {}
        self._display_queues: dict[str, LatestFrameQueue] = {}
        self._errors: dict[str, Exception] = {}
        # Cameras the user asked to stream (survives worker failure; used by auto-reconnect).
        self._wanted_streaming: set[str] = set()
        # Kept here (not only on the worker) so recording survives stream restarts.
        self._recording_queues: dict[str, RecordingQueue] = {}

    # --- registry ---------------------------------------------------------
    def add_camera(self, camera: CameraDevice) -> None:
        with self._lock:
            if camera.camera_id in self._cameras:
                raise ValueError(f"Camera {camera.camera_id!r} already added")
            self._cameras[camera.camera_id] = camera

    def remove_camera(self, camera_id: str) -> None:
        with self._lock:
            self.disconnect(camera_id)
            del self._cameras[camera_id]
            self._errors.pop(camera_id, None)

    def camera(self, camera_id: str) -> CameraDevice:
        with self._lock:
            return self._cameras[camera_id]

    @property
    def camera_ids(self) -> list[str]:
        with self._lock:
            return list(self._cameras)

    # --- lifecycle --------------------------------------------------------
    def connect(self, camera_id: str) -> None:
        camera = self.camera(camera_id)
        with self._lock:
            self._errors.pop(camera_id, None)
        camera.connect()
        logger.info("Connected %s (%s, S/N %s)", camera_id, camera.model, camera.serial_number)

    def disconnect(self, camera_id: str) -> None:
        camera = self.camera(camera_id)
        self.stop_streaming(camera_id)
        camera.disconnect()
        logger.info("Disconnected %s", camera_id)

    def start_streaming(self, camera_id: str) -> None:
        camera = self.camera(camera_id)
        with self._lock:
            self._wanted_streaming.add(camera_id)
            worker = self._workers.get(camera_id)
            if worker is not None and worker.running:
                return
            self._errors.pop(camera_id, None)
            display_queue = LatestFrameQueue(self._display_queue_size)
            worker = AcquisitionWorker(
                camera,
                display_queue,
                frame_timeout=self._frame_timeout,
                on_error=self._on_worker_error,
            )
            worker.recording_queue = self._recording_queues.get(camera_id)
            self._display_queues[camera_id] = display_queue
            self._workers[camera_id] = worker
        worker.start()

    def stop_streaming(self, camera_id: str) -> None:
        with self._lock:
            self._wanted_streaming.discard(camera_id)
            worker = self._workers.pop(camera_id, None)
        if worker is not None:
            worker.stop()

    def reconnect(self, camera_id: str) -> None:
        """Re-open a camera after a failure and resume streaming if it was streaming.

        Raises CameraError if the camera is still unavailable; state stays ERROR in that case.
        """
        camera = self.camera(camera_id)
        with self._lock:
            resume = camera_id in self._wanted_streaming
            worker = self._workers.pop(camera_id, None)
        if worker is not None:
            worker.stop()
        try:
            camera.disconnect()  # release a stale handle
        except CameraError as exc:
            logger.debug("Cleanup before reconnecting %s: %s", camera_id, exc)
        camera.connect()
        with self._lock:
            self._errors.pop(camera_id, None)
        logger.info("Reconnected %s", camera_id)
        if resume:
            self.start_streaming(camera_id)

    def set_recording_queue(self, camera_id: str, recording_queue: RecordingQueue | None) -> None:
        """Route frames from ``camera_id`` into ``recording_queue`` (None to detach).

        The queue stays attached across stop/start of streaming until detached.
        """
        self.camera(camera_id)  # KeyError for unknown cameras
        with self._lock:
            if recording_queue is None:
                self._recording_queues.pop(camera_id, None)
            else:
                self._recording_queues[camera_id] = recording_queue
            worker = self._workers.get(camera_id)
        if worker is not None:
            worker.recording_queue = recording_queue

    def snapshot_frame(self, camera_id: str) -> Frame | None:
        """Most recent frame from a streaming camera (does not consume the display queue)."""
        with self._lock:
            worker = self._workers.get(camera_id)
        return worker.last_frame if worker is not None else None

    def shutdown(self) -> None:
        for camera_id in self.camera_ids:
            try:
                self.disconnect(camera_id)
            except CameraError as exc:
                logger.warning("Error disconnecting %s during shutdown: %s", camera_id, exc)

    # --- queries (cheap; safe to call every UI frame) ---------------------
    def latest_frame(self, camera_id: str) -> Frame | None:
        with self._lock:
            display_queue = self._display_queues.get(camera_id)
        return display_queue.get_latest() if display_queue is not None else None

    def stats(self, camera_id: str) -> AcquisitionStats | None:
        with self._lock:
            worker = self._workers.get(camera_id)
        return worker.stats() if worker is not None else None

    def state(self, camera_id: str) -> CameraState:
        camera = self.camera(camera_id)
        with self._lock:
            if camera_id in self._errors:
                return CameraState.ERROR
            worker = self._workers.get(camera_id)
        if worker is not None and worker.running:
            return CameraState.ACQUIRING
        return CameraState.CONNECTED if camera.connected else CameraState.DISCONNECTED

    def last_error(self, camera_id: str) -> str | None:
        with self._lock:
            exc = self._errors.get(camera_id)
        return None if exc is None else (str(exc) or type(exc).__name__)

    def last_exception(self, camera_id: str) -> Exception | None:
        with self._lock:
            return self._errors.get(camera_id)

    def wants_streaming(self, camera_id: str) -> bool:
        with self._lock:
            return camera_id in self._wanted_streaming

    def _on_worker_error(self, camera_id: str, exc: Exception) -> None:
        with self._lock:
            self._errors[camera_id] = exc
