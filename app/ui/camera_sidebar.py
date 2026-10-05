"""Camera list: one CameraRow per detected camera, each with its own expandable controls."""

from __future__ import annotations

from collections.abc import Callable

import dearpygui.dearpygui as dpg

from app.cameras.camera_manager import CameraManager
from app.services.camera_status_service import CameraStatusService
from app.services.network_service import NetworkService
from app.services.recording_service import RecordingService
from app.ui.camera_row import CameraRow
from app.ui.theme import caption, secondary_text


class CameraSidebar:
    def __init__(
        self,
        parent: int | str,
        manager: CameraManager,
        statuses: CameraStatusService,
        recording: RecordingService,
        network: NetworkService,
        on_property_grid: Callable[[str], None],
    ) -> None:
        self._manager = manager
        self.selected: str | None = None
        self.rows: dict[str, CameraRow] = {}

        with dpg.group(horizontal=True, parent=parent):
            caption("Cameras")
            self._count = secondary_text(str(len(manager.camera_ids)))
        if not manager.camera_ids:
            secondary_text("No cameras discovered", parent=parent)
        for status in statuses.statuses():
            self.rows[status.camera_id] = CameraRow(
                parent, status, manager, statuses, recording, network,
                on_select=self.select, on_property_grid=on_property_grid,
            )

        if self.rows:
            self.select(next(iter(self.rows)))

    def update(self) -> None:
        for row in self.rows.values():
            row.update()

    def select(self, camera_id: str) -> None:
        self.selected = camera_id
        for cid, row in self.rows.items():
            row.set_selected(cid == camera_id)

    @property
    def busy(self) -> bool:
        return any(row.busy for row in self.rows.values())

    def is_expanded(self, camera_id: str) -> bool:
        return self.rows[camera_id].expanded

    def set_expanded(self, camera_id: str, expanded: bool) -> None:
        self.rows[camera_id].set_expanded(expanded)
