"""Camera list: one CameraRow per camera, each with its own expandable controls.

Rows are added live when the discovery service finds a newly connected camera (hot-plug).
"""

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
        self._parent = parent
        self._manager = manager
        self._statuses = statuses
        self._recording = recording
        self._network = network
        self._on_property_grid = on_property_grid
        self.selected: str | None = None
        self.rows: dict[str, CameraRow] = {}

        with dpg.group(horizontal=True, parent=parent):
            caption("Cameras")
            self._count = secondary_text("0")
        self._empty = secondary_text("No cameras found yet. Connect a camera: it appears here automatically.",
                                     parent=parent, wrap=300)
        self._add_new_rows()

    def update(self) -> None:
        if len(self.rows) != len(self._manager.camera_ids):
            self._add_new_rows()
        for row in self.rows.values():
            row.update()

    def _add_new_rows(self) -> None:
        for camera_id in self._manager.camera_ids:
            if camera_id in self.rows:
                continue
            self.rows[camera_id] = CameraRow(
                self._parent, self._statuses.status(camera_id), self._manager, self._statuses, self._recording,
                self._network, on_select=self.select, on_property_grid=self._on_property_grid,
            )
        dpg.set_value(self._count, str(len(self.rows)))
        dpg.configure_item(self._empty, show=not self.rows)
        if self.selected is None and self.rows:
            self.select(next(iter(self.rows)))

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
