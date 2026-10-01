"""Builds CameraStatus snapshots from CameraManager state and acquisition statistics.

Single source of per-camera status for the UI (sidebar, status panel, performance window).
Cheap: reads cached device identity and worker counters, never touches the camera.
"""

from __future__ import annotations

from app.cameras.camera_manager import CameraManager
from app.models.camera_status import CameraStatus


class CameraStatusService:
    def __init__(self, manager: CameraManager) -> None:
        self._manager = manager

    def status(self, camera_id: str) -> CameraStatus:
        camera = self._manager.camera(camera_id)
        stats = self._manager.stats(camera_id)
        return CameraStatus(
            camera_id=camera_id,
            model=camera.model,
            serial_number=camera.serial_number,
            ip_address=camera.ip_address,
            state=self._manager.state(camera_id),
            bandwidth_mbps=stats.bandwidth_mbps if stats else 0.0,
            fps=stats.measured_fps if stats else 0.0,
            frame_count=stats.frames_acquired if stats else 0,
            frames_missed=stats.frames_missed if stats else 0,
            timeouts=stats.timeouts if stats else 0,
            error=self._manager.last_error(camera_id),
        )

    def statuses(self) -> list[CameraStatus]:
        return [self.status(camera_id) for camera_id in self._manager.camera_ids]
