from __future__ import annotations

from dataclasses import dataclass

from app.models.camera_state import CameraState


def camera_display_name(model: str, serial_number: str) -> str:
    """``Model (Serial)``: the camera name used everywhere in the UI."""
    return f"{model} ({serial_number})"


@dataclass(frozen=True)
class CameraStatus:
    """Application-level snapshot of one camera for the UI (no SDK objects)."""

    camera_id: str
    model: str
    serial_number: str
    ip_address: str | None
    state: CameraState  # connection + acquisition state
    bandwidth_mbps: float = 0.0  # megabits per second of image data received
    fps: float = 0.0
    frame_count: int = 0  # frames received since streaming started
    frames_missed: int = 0
    timeouts: int = 0
    error: str | None = None

    @property
    def display_name(self) -> str:
        return camera_display_name(self.model, self.serial_number)

    @property
    def connected(self) -> bool:
        return self.state in (CameraState.CONNECTED, CameraState.ACQUIRING)

    @property
    def acquiring(self) -> bool:
        return self.state is CameraState.ACQUIRING
