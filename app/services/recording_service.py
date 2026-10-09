"""Recordings and image captures for the UI.

Cameras record independently (camera A recording while B is not); a camera belongs to at most one
active session. Everything is filed by day, then by camera (one folder per physical camera, its
number kept for the whole day), so all cameras' recordings and images of a day sit side by side:

    <save folder>/YYYY-MM-DD/
        Camera_01_TRI122S-C_262503318/
            Recording_20261009_101523.mp4      (raw: .raw + .csv)
            Recording_20261009_101523.csv/.json  (info files, when recording.save_metadata is on)
            Images/TRI122S-C_262503318_20261009_101700_123_f42.png ...
        Camera_02_TRI122S-C_262503319/ ...
"""

from __future__ import annotations

import json
import logging
import re
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
IMAGES_FOLDER = "Images"
_CAMERA_FOLDER = re.compile(r"Camera_(\d+)_(.+)")


def _safe_name(text: str) -> str:
    """Folder-name-safe text (model / serial): letters, digits, '.', '-' and '_' only."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-") or "camera"


def _unique_stem(folder: Path, stem: str) -> str:
    """``stem``, or ``stem_2``, ``stem_3`` ... when a recording with that name already exists."""
    candidate, n = stem, 2
    while any(folder.glob(f"{candidate}.*")):
        candidate, n = f"{stem}_{n}", n + 1
    return candidate
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
    files: dict[str, Path] = field(default_factory=dict)  # camera id -> its recording file

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
    directory: Path  # the camera's folder
    elapsed_s: float
    stats: RecorderStats
    file: Path | None = None  # the recording file


@dataclass
class _Session:
    mode: RecordingMode
    directory: Path
    started: float
    recorders: dict[str, CameraRecorder]
    camera_dirs: dict[str, str]  # camera id -> its folder name in the day folder
    metadata: dict[str, dict]
    final_stats: dict[str, RecorderStats] = field(default_factory=dict)  # cameras already stopped
    save_metadata: bool = False  # .csv for video + .json info files (setting at start time)
    files: dict[str, Path] = field(default_factory=dict)  # camera id -> recording file

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
            self._base_dir = Path(rec_cfg["directory"])  # recordings and images (one save folder)
            self._queue_frames = int(rec_cfg["queue_frames"])
            self._min_free_bytes = int(float(rec_cfg["min_free_gb"]) * 1e9)
            self.default_mode = RecordingMode(rec_cfg["mode"])
            self._save_metadata = bool(rec_cfg.get("save_metadata", False))
            self.timestamp_overlay = bool(rec_cfg.get("timestamp_overlay", True))
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
                                   time.time() - session.started, session.recorders[camera_id].stats(),
                                   session.files.get(camera_id))

    def start(self, mode: RecordingMode | None = None, camera_ids: list[str] | None = None,
              timestamp_overlay: bool | None = None) -> Path:
        """Start a session for ``camera_ids`` (default: all streaming cameras not already recording).

        ``timestamp_overlay``: burn the real-time timestamp into video frames (default: the
        ``recording.timestamp_overlay`` setting). Ignored for raw, which is never altered.

        Returns the day folder (each camera records into its own folder in it). Other cameras'
        recordings are not affected.
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
            stamp = self.timestamp_overlay if timestamp_overlay is None else bool(timestamp_overlay)
            directory = self.day_folder(started)
            recorders: dict[str, CameraRecorder] = {}
            camera_dirs: dict[str, str] = {}
            metadata: dict[str, dict] = {}
            files: dict[str, Path] = {}
            for camera_id in camera_ids:
                camera = self._manager.camera(camera_id)
                folder = self.camera_folder(camera_id, started)
                stem = _unique_stem(folder, time.strftime("Recording_%Y%m%d_%H%M%S", time.localtime(started)))
                metadata[camera_id] = camera_metadata(camera)
                if mode is not RecordingMode.RAW:
                    metadata[camera_id]["timestamp_overlay"] = stamp
                writer = (
                    RawSequenceWriter(folder, stem=stem)
                    if mode is RecordingMode.RAW
                    else VideoFileWriter(folder, fps=camera.frame_rate or 30.0, container=_CONTAINER[mode],
                                         index=self._save_metadata, stamp=stamp, stem=stem)
                )
                recorder = CameraRecorder(camera_id, writer, RecordingQueue(self._queue_frames))
                recorder.start()
                recorders[camera_id] = recorder
                camera_dirs[camera_id] = folder.name
                files[camera_id] = writer.path
            session = _Session(mode, directory, started, recorders, camera_dirs, metadata,
                               save_metadata=self._save_metadata, files=files)
            self._sessions.append(session)
            self._last_error = None
            for camera_id in camera_ids:
                self._write_info(session, camera_id)
            # Attach queues last so recorders are ready before frames arrive.
            for camera_id, recorder in recorders.items():
                self._manager.set_recording_queue(camera_id, recorder.queue)
            logger.info("Recording (%s) %s to %s", mode.label, ", ".join(camera_ids), directory, extra=for_camera(camera_ids[0] if len(camera_ids) == 1 else '-'))
            return directory

    def stop(self, camera_ids: list[str] | None = None) -> RecordingStatus:
        """Stop recording ``camera_ids`` (default: every recording camera).

        Detaches queues, flushes what is already queued, and finalises each camera's info file.
        Returns totals (and the recording files) for the cameras that were stopped.
        """
        with self._lock:
            targets = set(camera_ids) if camera_ids is not None else None
            stopped: dict[str, RecorderStats] = {}
            files: dict[str, Path] = {}
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
                    files[camera_id] = session.files[camera_id]
                    self._write_info(session, camera_id, stopped=time.time())
                finished = not session.active_cameras
                if finished:
                    self._sessions.remove(session)
                last_dir, mode = session.directory, session.mode
                totals = RecordingStatus(active=False, cameras={c: stopped[c] for c in to_stop})
                logger.info(  # tagged with the camera when only one stopped
                    "Recording stopped for %s: %d frames, %.2f GB, %d dropped -> %s",
                    ", ".join(to_stop), totals.frames_written, totals.bytes_written / 1e9, totals.dropped,
                    ", ".join(str(session.files[c]) for c in to_stop),
                    extra=for_camera(to_stop[0] if len(to_stop) == 1 else "-"),
                )
            return RecordingStatus(active=self.active, mode=mode, session_dir=last_dir, cameras=stopped,
                                   free_bytes=self._free_bytes, error=self._last_error, files=files)

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

    def day_folder(self, when: float | None = None) -> Path:
        """<save folder>/YYYY-MM-DD for ``when`` (default now)."""
        return self._base_dir / time.strftime("%Y-%m-%d", time.localtime(time.time() if when is None else when))

    def camera_folder(self, camera_id: str, when: float | None = None) -> Path:
        """The camera's folder in the day folder, created on first use: Camera_NN_<model>_<serial>.

        A camera keeps its number for the whole day (found again by model + serial); a camera seen
        for the first time that day gets the next number."""
        camera = self._manager.camera(camera_id)
        identity = f"{_safe_name(camera.model)}_{_safe_name(camera.serial_number)}"
        day = self.day_folder(when)
        with self._lock:
            numbers = []
            if day.exists():
                for entry in day.iterdir():
                    match = _CAMERA_FOLDER.fullmatch(entry.name)
                    if not match or not entry.is_dir():
                        continue
                    if match.group(2) == identity:
                        return entry
                    numbers.append(int(match.group(1)))
            folder = day / f"Camera_{max(numbers, default=0) + 1:02d}_{identity}"
            folder.mkdir(parents=True, exist_ok=True)
            return folder

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

    def _write_info(self, session: _Session, camera_id: str, stopped: float | None = None) -> None:
        """<recording>.json next to the camera's recording (only with recording.save_metadata)."""
        if not session.save_metadata:
            return
        file = session.files[camera_id]
        stats = session.final_stats.get(camera_id) or session.recorders[camera_id].stats()
        if session.mode is RecordingMode.RAW:
            description = f"raw frame sequence ({file.stem}.raw + {file.stem}.csv)"
        else:
            description = f"{session.mode.label} video, half resolution, + {file.stem}.csv"
        document = {
            "application": {"name": APP_NAME, "version": __version__},
            "recording": {
                "file": file.name,
                "mode": session.mode.value,
                "format": description,
                "started": _iso(session.started),
                "stopped": _iso(stopped) if stopped else None,
                "frames_written": stats.frames_written,
                "bytes_written": stats.bytes_written,
                "frame_gaps": stats.frame_gaps,
                "queue_overflows": stats.queue_overflows,
                "error": stats.error,
            },
            "camera": session.metadata[camera_id],
        }
        file.with_suffix(".json").write_text(json.dumps(document, indent=2), encoding="utf-8")

    # --- snapshots ------------------------------------------------------------
    def snapshot(self, camera_ids: list[str] | None = None, image_format: str = "png") -> list[SnapshotFiles]:
        """Save the latest frame of each camera (default: all streaming) in ``image_format``."""
        if image_format not in IMAGE_FORMATS:
            raise RecordingError(f"Unsupported image format {image_format!r}; choose from {list(IMAGE_FORMATS)}")
        if camera_ids is None:
            camera_ids = [c for c in self._manager.camera_ids if self._manager.state(c) is CameraState.ACQUIRING]
        saved = []
        for camera_id in camera_ids:
            frame = self._manager.snapshot_frame(camera_id)
            if frame is None:
                continue
            camera = self._manager.camera(camera_id)
            directory = self.camera_folder(camera_id, frame.timestamp) / IMAGES_FOLDER
            metadata = {"application": {"name": APP_NAME, "version": __version__},
                        "camera": camera_metadata(camera)}
            files = save_snapshot(frame, directory, metadata, image_format,
                                  name=f"{_safe_name(camera.model)}_{_safe_name(camera.serial_number)}")
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
