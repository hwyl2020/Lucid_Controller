"""Camera settings for the UI: read a snapshot of values + capabilities, apply changes.

Sits between the controls UI and CameraManager/CameraDevice so the UI never calls devices directly.
Settings that real cameras lock during streaming (pixel format, ROI) are applied by pausing the
stream and resuming it afterwards.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from app.cameras.camera_device import NumericRange, Roi, RoiLimits, UnsupportedFeatureError
from app.cameras.camera_manager import CameraManager
from app.models.camera_state import CameraState

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ControlSnapshot:
    camera_id: str
    connected: bool
    streaming: bool
    exposure: float | None
    exposure_range: NumericRange | None
    gain: float | None
    gain_range: NumericRange | None
    frame_rate: float | None
    frame_rate_range: NumericRange | None
    pixel_format: str
    pixel_formats: list[str]
    roi: Roi | None
    roi_limits: RoiLimits | None


class CameraControlService:
    def __init__(self, manager: CameraManager) -> None:
        self._manager = manager

    def snapshot(self, camera_id: str) -> ControlSnapshot:
        """Read current values and capabilities. Touches the device; don't call every frame."""
        camera = self._manager.camera(camera_id)
        streaming = self._manager.state(camera_id) is CameraState.ACQUIRING
        if not camera.connected:
            return ControlSnapshot(camera_id, False, streaming, None, None, None, None, None, None, "", [], None, None)
        limits = camera.roi_limits()
        return ControlSnapshot(
            camera_id=camera_id,
            connected=True,
            streaming=streaming,
            exposure=camera.exposure,
            exposure_range=camera.exposure_range(),
            gain=camera.gain,
            gain_range=camera.gain_range(),
            frame_rate=camera.frame_rate,
            frame_rate_range=camera.frame_rate_range(),
            pixel_format=camera.pixel_format,
            pixel_formats=camera.pixel_formats(),
            roi=camera.roi if limits is not None else None,
            roi_limits=limits,
        )

    # Values are clamped/snapped to the camera's range so typing "slightly off" values works;
    # the applied value is returned so the UI can show what the camera actually got.
    def set_exposure(self, camera_id: str, value: float) -> float:
        camera = self._manager.camera(camera_id)
        value = self._clamp(camera.exposure_range(), value, "exposure")
        camera.set_exposure(value)
        return value

    def set_gain(self, camera_id: str, value: float) -> float:
        camera = self._manager.camera(camera_id)
        value = self._clamp(camera.gain_range(), value, "gain")
        camera.set_gain(value)
        return value

    def set_frame_rate(self, camera_id: str, value: float) -> float:
        camera = self._manager.camera(camera_id)
        value = self._clamp(camera.frame_rate_range(), value, "frame rate")
        camera.set_frame_rate(value)
        return value

    def set_pixel_format(self, camera_id: str, pixel_format: str) -> None:
        camera = self._manager.camera(camera_id)
        if pixel_format == camera.pixel_format:
            return
        self._with_stream_paused(camera_id, lambda: camera.set_pixel_format(pixel_format))

    def set_roi(self, camera_id: str, roi: Roi) -> Roi:
        camera = self._manager.camera(camera_id)
        limits = camera.roi_limits()
        if limits is None:
            raise UnsupportedFeatureError(f"{camera_id}: ROI not supported")
        roi = limits.clamp(roi)
        if roi != camera.roi:
            self._with_stream_paused(camera_id, lambda: camera.set_roi(roi.x, roi.y, roi.width, roi.height))
        return roi

    def reset_roi(self, camera_id: str) -> Roi:
        limits = self._manager.camera(camera_id).roi_limits()
        if limits is None:
            raise UnsupportedFeatureError(f"{camera_id}: ROI not supported")
        return self.set_roi(camera_id, limits.full_frame())

    def _clamp(self, value_range: NumericRange | None, value: float, label: str) -> float:
        if value_range is None:
            raise UnsupportedFeatureError(f"{label} is not supported by this camera")
        return value_range.clamp(float(value))

    def _with_stream_paused(self, camera_id: str, action: Callable[[], None]) -> None:
        was_streaming = self._manager.state(camera_id) is CameraState.ACQUIRING
        if was_streaming:
            self._manager.stop_streaming(camera_id)
        try:
            action()
        finally:
            if was_streaming:
                self._manager.start_streaming(camera_id)
