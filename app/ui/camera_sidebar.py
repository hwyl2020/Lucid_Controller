"""Camera list: one CameraRow per detected camera (each with its own expandable controls), plus
Start all / Stop all. Tracks the selected camera for the settings panel below the list."""

from __future__ import annotations

from collections.abc import Callable

import dearpygui.dearpygui as dpg

from app.cameras.camera_manager import CameraManager
from app.services.camera_status_service import CameraStatusService
from app.services.recording_service import RecordingService
from app.ui.camera_row import CameraRow
from app.ui.theme import TEXT_DIM, use_font


class CameraSidebar:
    def __init__(
        self,
        parent: int | str,
        manager: CameraManager,
        statuses: CameraStatusService,
        recording: RecordingService,
        on_property_grid: Callable[[str], None],
    ) -> None:
        self._manager = manager
        self.selected: str | None = None
        self.rows: dict[str, CameraRow] = {}

        title = dpg.add_text("CAMERAS", color=TEXT_DIM, parent=parent)
        use_font(title, "heading")
        dpg.add_spacer(height=2, parent=parent)
        if not manager.camera_ids:
            dpg.add_text("No cameras discovered", color=TEXT_DIM, parent=parent)
        for status in statuses.statuses():
            self.rows[status.camera_id] = CameraRow(
                parent, status, manager, statuses, recording, on_select=self.select, on_property_grid=on_property_grid
            )
            dpg.add_spacer(height=2, parent=parent)

        dpg.add_spacer(height=4, parent=parent)
        with dpg.group(horizontal=True, parent=parent):
            dpg.add_button(label="Start all", width=150, callback=self.start_all)
            dpg.add_button(label="Stop all", width=150, callback=self.stop_all)

        # The settings panel below acts on the selection; preselect the first camera.
        if self.rows:
            self.select(next(iter(self.rows)))

    def update(self) -> None:
        for row in self.rows.values():
            row.update()

    def select(self, camera_id: str) -> None:
        self.selected = camera_id
        for cid, row in self.rows.items():
            row.set_selected(cid == camera_id)

    def start_all(self) -> None:
        for row in self.rows.values():
            row.start()

    def stop_all(self) -> None:
        for row in self.rows.values():
            row.stop()

    @property
    def busy(self) -> bool:
        return any(row.busy for row in self.rows.values())

    def is_expanded(self, camera_id: str) -> bool:
        return self.rows[camera_id].expanded

    def set_expanded(self, camera_id: str, expanded: bool) -> None:
        self.rows[camera_id].set_expanded(expanded)
