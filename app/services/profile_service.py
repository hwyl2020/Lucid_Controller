"""Camera settings profiles: capture a camera's settings, save them by name, apply them later.

Profiles live in ``profiles/<name>.json``. Applying is best-effort: each setting is applied through
CameraControlService (so values are clamped to the target camera's limits, and pixel format/ROI
pause the stream as needed); anything the target camera cannot take becomes a warning.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from app.cameras.camera_device import CameraError, Roi
from app.cameras.camera_manager import CameraManager
from app.services.camera_control_service import CameraControlService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CameraSettings:
    pixel_format: str | None = None
    roi: Roi | None = None
    exposure_us: float | None = None
    gain_db: float | None = None
    frame_rate_hz: float | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["roi"] = asdict(self.roi) if self.roi else None
        return data

    @classmethod
    def from_dict(cls, data: dict) -> CameraSettings:
        roi = data.get("roi")
        return cls(
            pixel_format=data.get("pixel_format"),
            roi=Roi(**roi) if roi else None,
            exposure_us=data.get("exposure_us"),
            gain_db=data.get("gain_db"),
            frame_rate_hz=data.get("frame_rate_hz"),
        )


class SettingsApplier:
    """Capture/apply CameraSettings on a connected camera."""

    def __init__(self, manager: CameraManager, controls: CameraControlService) -> None:
        self._manager = manager
        self._controls = controls

    def capture(self, camera_id: str) -> CameraSettings:
        camera = self._manager.camera(camera_id)
        if not camera.connected:
            raise CameraError(f"{camera_id}: connect the camera to read its settings")
        return CameraSettings(
            pixel_format=camera.pixel_format or None,
            roi=camera.roi if camera.roi_limits() is not None else None,
            exposure_us=camera.exposure,
            gain_db=camera.gain,
            frame_rate_hz=camera.frame_rate,
        )

    def apply(self, camera_id: str, settings: CameraSettings) -> list[str]:
        """Apply in a safe order; returns warnings for settings that could not be applied."""
        steps = [
            # Format and ROI first (they change the frame and may restart the stream); exposure
            # before frame rate because exposure limits the achievable frame rate.
            ("pixel format", settings.pixel_format, lambda v: self._controls.set_pixel_format(camera_id, v)),
            ("ROI", settings.roi, lambda v: self._controls.set_roi(camera_id, v)),
            ("exposure", settings.exposure_us, lambda v: self._controls.set_exposure(camera_id, v)),
            ("gain", settings.gain_db, lambda v: self._controls.set_gain(camera_id, v)),
            ("frame rate", settings.frame_rate_hz, lambda v: self._controls.set_frame_rate(camera_id, v)),
        ]
        warnings = []
        for label, value, apply in steps:
            if value is None:
                continue
            try:
                apply(value)
            except CameraError as exc:
                warnings.append(f"{label}: {exc}")
        for warning in warnings:
            logger.warning("Applying settings to %s: %s", camera_id, warning)
        return warnings


class ProfileService:
    def __init__(self, manager: CameraManager, applier: SettingsApplier, directory: Path) -> None:
        self._manager = manager
        self._applier = applier
        self._directory = Path(directory)

    def list_profiles(self) -> list[str]:
        if not self._directory.exists():
            return []
        names = []
        for path in sorted(self._directory.glob("*.json")):
            try:
                names.append(json.loads(path.read_text(encoding="utf-8"))["name"])
            except (OSError, ValueError, KeyError):
                logger.warning("Ignoring unreadable profile %s", path)
        return names

    def save(self, camera_id: str, name: str) -> Path:
        name = name.strip()
        if not name:
            raise ValueError("Profile name is empty")
        camera = self._manager.camera(camera_id)
        settings = self._applier.capture(camera_id)
        self._directory.mkdir(parents=True, exist_ok=True)
        path = self._path(name)
        document = {
            "name": name,
            "model": camera.model,
            "source_serial": camera.serial_number,
            "saved": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "settings": settings.to_dict(),
        }
        path.write_text(json.dumps(document, indent=2), encoding="utf-8")
        logger.info("Saved profile %r from %s to %s", name, camera_id, path)
        return path

    def apply(self, camera_id: str, name: str) -> list[str]:
        path = self._path(name)
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CameraError(f"Cannot read profile {name!r}: {exc}") from exc
        camera = self._manager.camera(camera_id)
        warnings = []
        if document.get("model") and document["model"] != camera.model:
            warnings.append(f"profile was saved from a {document['model']}; values were clamped to this {camera.model}")
        warnings += self._applier.apply(camera_id, CameraSettings.from_dict(document.get("settings", {})))
        logger.info("Applied profile %r to %s (%d warning(s))", name, camera_id, len(warnings))
        return warnings

    def _path(self, name: str) -> Path:
        slug = re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip()).strip("_") or "profile"
        return self._directory / f"{slug}.json"
