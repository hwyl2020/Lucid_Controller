"""Recording sessions and snapshots for the UI.

Session layout (per spec):
    <recordings>/YYYY-MM-DD/Session_YYYYMMDD_HHMMSS/Camera_01/ ... + session.json
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from app import __version__
from app.acquisition.frame_queue import RecordingQueue
from app.cameras.camera_device import CameraDevice, CameraError
from app.cameras.camera_manager import CameraManager
from app.models.camera_state import CameraState
from app.recording.recorder import CameraRecorder, RawSequenceWriter, RecorderStats, RecordingMode
from app.recording.snapshot import SnapshotFiles, save_snapshot
from app.recording.video_writer import VideoFileWriter

logger = logging.getLogger(__name__)

APP_NAME = "LUCID Camera Studio"
DISK_CHECK_INTERVAL_S = 1.0


class RecordingError(Exception):
    pass


@dataclass(frozen=True)
class RecordingStatus:
    active: bool
    mode: RecordingMode | None = None
    session_dir: Path | None = None
    elapsed_s: float = 0.0
    cameras: dict[str, RecorderStats] = field(default_factory=dict)
    free_bytes: int | None = None
    error: str | None = None  # last session-level problem (e.g. stopped for low disk space)

    @property
    def frames_written(self) -> int:
        return sum(s.frames_written for s in self.cameras.values())

    @property
    def bytes_written(self) -> int:
        return sum(s.bytes_written for s in self.cameras.values())

    @property
    def dropped(self) -> int:
        return sum(s.frame_gaps + s.queue_overflows for s in self.cameras.values())


@dataclass
class _Session:
    mode: RecordingMode
    directory: Path
    started: float
    recorders: dict[str, CameraRecorder]
    camera_dirs: dict[str, str]
    metadata: dict[str, dict]


class RecordingService:
    def __init__(self, manager: CameraManager, config: dict) -> None:
        self._manager = manager
        self._lock = threading.RLock()
        self.reconfigure(config)
        self._session: _Session | None = None
        self._last_error: str | None = None
        self._free_bytes: int | None = None

    def reconfigure(self, config: dict) -> None:
        """Apply recording/snapshot settings. Takes effect for the next recording."""
        rec_cfg = config["recording"]
        with self._lock:
            self._base_dir = Path(rec_cfg["directory"])
            self._snapshot_dir = Path(config["snapshots"]["directory"])
            self._queue_frames = int(rec_cfg["queue_frames"])
            self._min_free_bytes = int(float(rec_cfg["min_free_gb"]) * 1e9)
            self.default_mode = RecordingMode(rec_cfg["mode"])
            self._last_disk_check = 0.0

    # --- recording ----------------------------------------------------------
    @property
    def active(self) -> bool:
        return self._session is not None

    def is_recording(self, camera_id: str) -> bool:
        session = self._session
        return session is not None and camera_id in session.recorders

    def start(self, mode: RecordingMode | None = None, camera_ids: list[str] | None = None) -> Path:
        """Start recording the given cameras (default: all streaming). Returns the session dir."""
        mode = mode or self.default_mode
        with self._lock:
            if self._session is not None:
                raise RecordingError("Recording already in progress")
            if camera_ids is None:
                camera_ids = [c for c in self._manager.camera_ids if self._manager.state(c) is CameraState.ACQUIRING]
            if not camera_ids:
                raise RecordingError("No streaming cameras to record; start a camera first")

            free = self._check_disk(force=True)
            if free is not None and free < self._min_free_bytes:
                raise RecordingError(f"Not enough free disk space ({free / 1e9:.1f} GB)")

            started = time.time()
            local = time.localtime(started)
            directory = (
                self._base_dir / time.strftime("%Y-%m-%d", local) / time.strftime("Session_%Y%m%d_%H%M%S", local)
            )
            directory.mkdir(parents=True, exist_ok=False)

            recorders: dict[str, CameraRecorder] = {}
            camera_dirs: dict[str, str] = {}
            metadata: dict[str, dict] = {}
            for index, camera_id in enumerate(camera_ids, start=1):
                camera = self._manager.camera(camera_id)
                camera_dir = f"Camera_{index:02d}"
                metadata[camera_id] = camera_metadata(camera)
                writer = (
                    RawSequenceWriter(directory / camera_dir)
                    if mode is RecordingMode.RAW
                    else VideoFileWriter(directory / camera_dir, fps=camera.frame_rate or 30.0)
                )
                recorder = CameraRecorder(camera_id, writer, RecordingQueue(self._queue_frames))
                recorder.start()
                recorders[camera_id] = recorder
                camera_dirs[camera_id] = camera_dir
            self._session = _Session(mode, directory, started, recorders, camera_dirs, metadata)
            self._last_error = None
            self._write_session_json(stopped=None)
            # Attach queues last so recorders are ready before frames arrive.
            for camera_id, recorder in recorders.items():
                self._manager.set_recording_queue(camera_id, recorder.queue)
            logger.info("Recording (%s) %d camera(s) to %s", mode.value, len(recorders), directory)
            return directory

    def stop(self) -> RecordingStatus:
        """Detach queues, flush everything already queued, finalise session.json."""
        with self._lock:
            session = self._session
            if session is None:
                return self.status()
            for camera_id in session.recorders:
                try:
                    self._manager.set_recording_queue(camera_id, None)
                except KeyError:
                    pass
            for recorder in session.recorders.values():
                recorder.stop()
            status = self._status_for(session)
            self._write_session_json(stopped=time.time())
            self._session = None
            logger.info(
                "Recording stopped: %d frames, %.2f GB, %d dropped -> %s",
                status.frames_written, status.bytes_written / 1e9, status.dropped, session.directory,
            )
            return status

    def status(self) -> RecordingStatus:
        """Cheap enough to call every UI frame. Also enforces the free-space limit."""
        free = self._check_disk()
        session = self._session
        if session is None:
            return RecordingStatus(active=False, free_bytes=free, error=self._last_error)
        if free is not None and free < self._min_free_bytes:
            self._last_error = f"Recording stopped: free disk space below {self._min_free_bytes / 1e9:.1f} GB"
            logger.error(self._last_error)
            self.stop()
            return RecordingStatus(active=False, free_bytes=free, error=self._last_error)
        return self._status_for(session)

    def _status_for(self, session: _Session) -> RecordingStatus:
        cameras = {cid: r.stats() for cid, r in session.recorders.items()}
        errors = [f"{cid}: {s.error}" for cid, s in cameras.items() if s.error]
        return RecordingStatus(
            active=True,
            mode=session.mode,
            session_dir=session.directory,
            elapsed_s=time.time() - session.started,
            cameras=cameras,
            free_bytes=self._free_bytes,
            error="; ".join(errors) or self._last_error,
        )

    def _check_disk(self, force: bool = False) -> int | None:
        now = time.monotonic()
        if force or now - self._last_disk_check >= DISK_CHECK_INTERVAL_S:
            self._last_disk_check = now
            try:
                probe = self._base_dir if self._base_dir.exists() else self._base_dir.resolve().anchor
                self._free_bytes = shutil.disk_usage(probe).free
            except OSError as exc:
                logger.warning("Could not read free disk space: %s", exc)
                self._free_bytes = None
        return self._free_bytes

    def _write_session_json(self, stopped: float | None) -> None:
        session = self._session
        if session is None:
            return
        cameras = []
        for camera_id, recorder in session.recorders.items():
            stats = recorder.stats()
            cameras.append({
                "directory": session.camera_dirs[camera_id],
                **session.metadata[camera_id],
                "recording": {
                    "frames_written": stats.frames_written,
                    "bytes_written": stats.bytes_written,
                    "frame_gaps": stats.frame_gaps,
                    "queue_overflows": stats.queue_overflows,
                    "error": stats.error,
                },
            })
        document = {
            "application": {"name": APP_NAME, "version": __version__},
            "session": {
                "mode": session.mode.value,
                "format": "raw frame sequence (frames.raw + frames.csv)"
                if session.mode is RecordingMode.RAW
                else "MP4 (mp4v), half resolution, + frames.csv",
                "started": _iso(session.started),
                "stopped": _iso(stopped) if stopped else None,
            },
            "cameras": cameras,
        }
        (session.directory / "session.json").write_text(json.dumps(document, indent=2), encoding="utf-8")

    # --- snapshots ------------------------------------------------------------
    def snapshot(self, camera_ids: list[str] | None = None) -> list[SnapshotFiles]:
        """Save the latest frame of each camera (default: all streaming)."""
        if camera_ids is None:
            camera_ids = [c for c in self._manager.camera_ids if self._manager.state(c) is CameraState.ACQUIRING]
        directory = self._snapshot_dir / time.strftime("%Y-%m-%d")
        saved = []
        for camera_id in camera_ids:
            frame = self._manager.snapshot_frame(camera_id)
            if frame is None:
                continue
            metadata = {"application": {"name": APP_NAME, "version": __version__},
                        "camera": camera_metadata(self._manager.camera(camera_id))}
            files = save_snapshot(frame, directory, metadata)
            logger.info("Snapshot %s -> %s", camera_id, files.raw.parent)
            saved.append(files)
        if not saved:
            raise RecordingError("No frames available; start a camera first")
        return saved


def camera_metadata(camera: CameraDevice) -> dict:
    """Settings recorded alongside images. Reads the device, so not for per-frame use."""
    def safe(read):
        try:
            return read()
        except CameraError:
            return None

    roi = safe(lambda: camera.roi)
    return {
        "camera_id": camera.camera_id,
        "model": camera.model,
        "serial_number": camera.serial_number,
        "ip_address": camera.ip_address,
        "pixel_format": safe(lambda: camera.pixel_format),
        "roi": {"x": roi.x, "y": roi.y, "width": roi.width, "height": roi.height} if roi else None,
        "frame_rate_hz": safe(lambda: camera.frame_rate),
        "exposure_us": safe(lambda: camera.exposure),
        "gain_db": safe(lambda: camera.gain),
        "trigger": None,  # trigger configuration not implemented yet
    }


def _iso(timestamp: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(timestamp))
