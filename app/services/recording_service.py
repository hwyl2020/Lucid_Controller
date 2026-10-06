"""Recording sessions and snapshots for the UI.

Several sessions can run at once so cameras record independently (camera A recording while B is
not); a camera belongs to at most one active session. Session layout (per spec):
    <recordings>/YYYY-MM-DD/Session_YYYYMMDD_HHMMSS[_n]/Camera_01/ ... + session.json
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from app import APP_NAME, __version__
from app.acquisition.frame_queue import RecordingQueue
from app.cameras.camera_device import CameraDevice, CameraError
from app.cameras.camera_manager import CameraManager
from app.models.camera_state import CameraState
from app.recording.recorder import CameraRecorder, RawSequenceWriter, RecorderStats, RecordingMode
from app.recording.snapshot import IMAGE_FORMATS, SnapshotFiles, save_snapshot
from app.recording.video_writer import VideoFileWriter
from app.camera_log import for_camera

logger = logging.getLogger(__name__)

DISK_CHECK_INTERVAL_S = 1.0
_CONTAINER = {
    RecordingMode.VIDEO: "mp4",
    RecordingMode.AVI: "avi",
    RecordingMode.MOV: "mov",
    RecordingMode.MKV: "mkv",
}


class RecordingError(Exception):
    pass


@dataclass(frozen=True)
class RecordingStatus:
    """Aggregate over all active sessions (or, from stop(), over the cameras just stopped)."""

    active: bool
    mode: RecordingMode | None = None
    session_dir: Path | None = None
    elapsed_s: float = 0.0
    cameras: dict[str, RecorderStats] = field(default_factory=dict)
    free_bytes: int | None = None
    error: str | None = None  # last problem (e.g. stopped for low disk space)

    @property
    def frames_written(self) -> int:
        return sum(s.frames_written for s in self.cameras.values())

    @property
    def bytes_written(self) -> int:
        return sum(s.bytes_written for s in self.cameras.values())

    @property
    def dropped(self) -> int:
        return sum(s.frame_gaps + s.queue_overflows for s in self.cameras.values())


@dataclass(frozen=True)
class CameraRecording:
    """Recording state of one camera, for its row in the camera list."""

    mode: RecordingMode
    directory: Path
    elapsed_s: float
    stats: RecorderStats


@dataclass
class _Session:
    mode: RecordingMode
    directory: Path
    started: float
    recorders: dict[str, CameraRecorder]
    camera_dirs: dict[str, str]
    metadata: dict[str, dict]
    final_stats: dict[str, RecorderStats] = field(default_factory=dict)  # cameras already stopped
    save_metadata: bool = False  # frames.csv for video + session.json (setting at start time)

    @property
    def active_cameras(self) -> list[str]:
        return [cid for cid in self.recorders if cid not in self.final_stats]


class RecordingService:
    def __init__(self, manager: CameraManager, config: dict) -> None:
        self._manager = manager
        self._lock = threading.RLock()
        self._sessions: list[_Session] = []
        self._last_error: str | None = None
        self._free_bytes: int | None = None
        self.reconfigure(config)

    def reconfigure(self, config: dict) -> None:
        """Apply recording/snapshot settings. Takes effect for the next recording."""
        rec_cfg = config["recording"]
        with self._lock:
            self._base_dir = Path(rec_cfg["directory"])
            self._snapshot_dir = Path(config["snapshots"]["directory"])
            self._queue_frames = int(rec_cfg["queue_frames"])
            self._min_free_bytes = int(float(rec_cfg["min_free_gb"]) * 1e9)
            self.default_mode = RecordingMode(rec_cfg["mode"])
            self._save_metadata = bool(rec_cfg.get("save_metadata", False))
            self._last_disk_check = 0.0

    # --- recording ----------------------------------------------------------
    @property
    def active(self) -> bool:
        with self._lock:
            return any(s.active_cameras for s in self._sessions)

    def is_recording(self, camera_id: str) -> bool:
        return self._session_of(camera_id) is not None

    def camera_recording(self, camera_id: str) -> CameraRecording | None:
        with self._lock:
            session = self._session_of(camera_id)
            if session is None:
                return None
            return CameraRecording(session.mode, session.directory / session.camera_dirs[camera_id],
                                   time.time() - session.started, session.recorders[camera_id].stats())

    def start(self, mode: RecordingMode | None = None, camera_ids: list[str] | None = None) -> Path:
        """Start a session for ``camera_ids`` (default: all streaming cameras not already recording).

        Returns the session directory. Other cameras' recordings are not affected.
        """
        mode = mode or self.default_mode
        with self._lock:
            if camera_ids is None:
                camera_ids = [
                    c for c in self._manager.camera_ids
                    if self._manager.state(c) is CameraState.ACQUIRING and not self.is_recording(c)
                ]
                if not camera_ids:
                    raise RecordingError("No streaming cameras to record; start a camera first")
            busy = [c for c in camera_ids if self.is_recording(c)]
            if busy:
                raise RecordingError(f"Already recording: {', '.join(busy)}")
            idle = [c for c in camera_ids if self._manager.state(c) is not CameraState.ACQUIRING]
            if idle:
                raise RecordingError(f"Start the camera before recording: {', '.join(idle)}")

            free = self._check_disk(force=True)
            if free is not None and free < self._min_free_bytes:
                raise RecordingError(f"Not enough free disk space ({free / 1e9:.1f} GB)")

            started = time.time()
            directory = self._new_session_dir(started)
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
                    else VideoFileWriter(directory / camera_dir, fps=camera.frame_rate or 30.0, container=_CONTAINER[mode],
                                         index=self._save_metadata)
                )
                recorder = CameraRecorder(camera_id, writer, RecordingQueue(self._queue_frames))
                recorder.start()
                recorders[camera_id] = recorder
                camera_dirs[camera_id] = camera_dir
            session = _Session(mode, directory, started, recorders, camera_dirs, metadata,
                               save_metadata=self._save_metadata)
            self._sessions.append(session)
            self._last_error = None
            self._write_session_json(session)
            # Attach queues last so recorders are ready before frames arrive.
            for camera_id, recorder in recorders.items():
                self._manager.set_recording_queue(camera_id, recorder.queue)
            logger.info("Recording (%s) %s to %s", mode.label, ", ".join(camera_ids), directory, extra=for_camera(camera_ids[0] if len(camera_ids) == 1 else '-'))
            return directory

    def stop(self, camera_ids: list[str] | None = None) -> RecordingStatus:
        """Stop recording ``camera_ids`` (default: every recording camera).

        Detaches queues, flushes what is already queued, and updates session.json (finalised when the
        session's last camera stops). Returns totals for the cameras that were stopped.
        """
        with self._lock:
            targets = set(camera_ids) if camera_ids is not None else None
            stopped: dict[str, RecorderStats] = {}
            last_dir: Path | None = None
            mode: RecordingMode | None = None
            for session in list(self._sessions):
                to_stop = [c for c in session.active_cameras if targets is None or c in targets]
                if not to_stop:
                    continue
                for camera_id in to_stop:
                    try:
                        self._manager.set_recording_queue(camera_id, None)
                    except KeyError:
                        pass
                for camera_id in to_stop:
                    session.final_stats[camera_id] = session.recorders[camera_id].stop()
                    stopped[camera_id] = session.final_stats[camera_id]
                finished = not session.active_cameras
                self._write_session_json(session, stopped=time.time() if finished else None)
                if finished:
                    self._sessions.remove(session)
                last_dir, mode = session.directory, session.mode
                totals = RecordingStatus(active=False, cameras={c: stopped[c] for c in to_stop})
                logger.info(  # tagged with the camera when only one stopped
                    "Recording stopped for %s: %d frames, %.2f GB, %d dropped -> %s",
                    ", ".join(to_stop), totals.frames_written, totals.bytes_written / 1e9, totals.dropped, session.directory,
                    extra=for_camera(to_stop[0] if len(to_stop) == 1 else "-"),
                )
            return RecordingStatus(active=self.active, mode=mode, session_dir=last_dir, cameras=stopped,
                                   free_bytes=self._free_bytes, error=self._last_error)

    def status(self) -> RecordingStatus:
        """Aggregate over active sessions. Cheap enough per UI frame; enforces the free-space limit."""
        free = self._check_disk()
        with self._lock:
            sessions = [s for s in self._sessions if s.active_cameras]
            if not sessions:
                return RecordingStatus(active=False, free_bytes=free, error=self._last_error)
            if free is not None and free < self._min_free_bytes:
                self._last_error = f"Recording stopped: free disk space below {self._min_free_bytes / 1e9:.1f} GB"
                logger.error(self._last_error)
                self.stop()
                return RecordingStatus(active=False, free_bytes=free, error=self._last_error)
            cameras = {cid: s.recorders[cid].stats() for s in sessions for cid in s.active_cameras}
            errors = [f"{cid}: {st.error}" for cid, st in cameras.items() if st.error]
            modes = {s.mode for s in sessions}
            return RecordingStatus(
                active=True,
                mode=modes.pop() if len(modes) == 1 else None,
                session_dir=sessions[-1].directory,
                elapsed_s=time.time() - min(s.started for s in sessions),
                cameras=cameras,
                free_bytes=self._free_bytes,
                error="; ".join(errors) or self._last_error,
            )

    def _session_of(self, camera_id: str) -> _Session | None:
        with self._lock:
            return next((s for s in self._sessions if camera_id in s.active_cameras), None)

    def _new_session_dir(self, started: float) -> Path:
        local = time.localtime(started)
        parent = self._base_dir / time.strftime("%Y-%m-%d", local)
        name = time.strftime("Session_%Y%m%d_%H%M%S", local)
        directory, n = parent / name, 2
        while directory.exists():  # two cameras started within the same second
            directory, n = parent / f"{name}_{n}", n + 1
        directory.mkdir(parents=True)
        return directory

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

    def _write_session_json(self, session: _Session, stopped: float | None = None) -> None:
        if not session.save_metadata:
            return
        cameras = []
        for camera_id, recorder in session.recorders.items():
            stats = session.final_stats.get(camera_id) or recorder.stats()
            cameras.append({
                "directory": session.camera_dirs[camera_id],
                **session.metadata[camera_id],
                "recording": {
                    "frames_written": stats.frames_written,
                    "bytes_written": stats.bytes_written,
                    "frame_gaps": stats.frame_gaps,
                    "queue_overflows": stats.queue_overflows,
                    "error": stats.error,
                    "stopped": camera_id in session.final_stats,
                },
            })
        if session.mode is RecordingMode.RAW:
            description = "raw frame sequence (frames.raw + frames.csv)"
        else:
            description = f"{session.mode.label} video, half resolution, + frames.csv"
        document = {
            "application": {"name": APP_NAME, "version": __version__},
            "session": {
                "mode": session.mode.value,
                "format": description,
                "started": _iso(session.started),
                "stopped": _iso(stopped) if stopped else None,
            },
            "cameras": cameras,
        }
        (session.directory / "session.json").write_text(json.dumps(document, indent=2), encoding="utf-8")

    # --- snapshots ------------------------------------------------------------
    def snapshot(self, camera_ids: list[str] | None = None, image_format: str = "png") -> list[SnapshotFiles]:
        """Save the latest frame of each camera (default: all streaming) in ``image_format``."""
        if image_format not in IMAGE_FORMATS:
            raise RecordingError(f"Unsupported image format {image_format!r}; choose from {list(IMAGE_FORMATS)}")
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
            files = save_snapshot(frame, directory, metadata, image_format)
            logger.info("Snapshot %s (%s) -> %s", camera_id, image_format.upper(), files.processed or files.raw, extra=for_camera(camera_id))
            saved.append(files)
        if not saved:
            raise RecordingError("No frames available; start the camera first")
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
