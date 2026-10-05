"""Workspace sessions: layout, which cameras stream, and each camera's settings, saved by name.

Sessions live in ``sessions/<name>.json``. Loading reconnects the cameras that are present, applies
their settings and restarts streaming for those that were streaming; missing cameras are reported.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from app import __version__
from app.cameras.camera_device import CameraError
from app.cameras.camera_manager import CameraManager
from app.models.camera_state import CameraState
from app.services.profile_service import CameraSettings, SettingsApplier
from app.camera_log import for_camera

logger = logging.getLogger(__name__)


@dataclass
class SessionLoadResult:
    layout: str | None
    warnings: list[str] = field(default_factory=list)
    started: list[str] = field(default_factory=list)


class SessionManager:
    def __init__(self, manager: CameraManager, applier: SettingsApplier, directory: Path) -> None:
        self._manager = manager
        self._applier = applier
        self._directory = Path(directory)

    def list_sessions(self) -> list[str]:
        if not self._directory.exists():
            return []
        return [p.stem for p in sorted(self._directory.glob("*.json"))]

    def save(self, name: str, layout: str) -> Path:
        cameras = {}
        for camera_id in self._manager.camera_ids:
            camera = self._manager.camera(camera_id)
            entry = {
                "model": camera.model,
                "serial_number": camera.serial_number,
                "streaming": self._manager.state(camera_id) is CameraState.ACQUIRING,
                "settings": None,
            }
            if camera.connected:
                try:
                    entry["settings"] = self._applier.capture(camera_id).to_dict()
                except CameraError as exc:
                    logger.warning("Session %r: could not read settings of %s: %s", name, camera_id, exc, extra=for_camera(camera_id))
            cameras[camera_id] = entry
        self._directory.mkdir(parents=True, exist_ok=True)
        path = self._path(name)
        document = {
            "name": name,
            "saved": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "application_version": __version__,
            "layout": layout,
            "cameras": cameras,
        }
        path.write_text(json.dumps(document, indent=2), encoding="utf-8")
        logger.info("Saved session %r (%d cameras) to %s", name, len(cameras), path)
        return path

    def load(self, name: str) -> SessionLoadResult:
        try:
            document = json.loads(self._path(name).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CameraError(f"Cannot read session {name!r}: {exc}") from exc

        result = SessionLoadResult(layout=document.get("layout"))
        present = set(self._manager.camera_ids)
        for camera_id, entry in document.get("cameras", {}).items():
            if camera_id not in present:
                result.warnings.append(f"{camera_id} ({entry.get('model', '?')}) is not connected to this PC")
                continue
            if not entry.get("settings") and not entry.get("streaming"):
                continue
            try:
                if not self._manager.camera(camera_id).connected:
                    self._manager.connect(camera_id)
                if entry.get("settings"):
                    warnings = self._applier.apply(camera_id, CameraSettings.from_dict(entry["settings"]))
                    result.warnings += [f"{camera_id}: {w}" for w in warnings]
                if entry.get("streaming"):
                    self._manager.start_streaming(camera_id)
                    result.started.append(camera_id)
            except CameraError as exc:
                result.warnings.append(f"{camera_id}: {exc}")
        logger.info("Loaded session %r: %d started, %d warning(s)", name, len(result.started), len(result.warnings))
        return result

    def _path(self, name: str) -> Path:
        slug = re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip()).strip("_") or "session"
        return self._directory / f"{slug}.json"
