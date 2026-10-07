"""Camera list: one CameraRow per camera, each with its own expandable controls.

Rows follow ``CameraManager.camera_ids``: added when a camera is connected (hot-plug discovery)
and removed when it is unplugged.
"""

from __future__ import annotations

from collections.abc import Callable

import dearpygui.dearpygui as dpg

from app.cameras.camera_manager import CameraManager
from app.services.camera_status_service import CameraStatusService
from app.services.network_service import NetworkService
from app.services.recording_service import RecordingService
from app.ui.camera_row import CameraRow
from app.ui import theme
from app.ui.theme import bind, secondary_text


class CameraSidebar:
    def __init__(
        self,
        parent: int | str,
        manager: CameraManager,
        statuses: CameraStatusService,
        recording: RecordingService,
        network: NetworkService,
        on_property_grid: Callable[[str], None],
        on_scan: Callable[[], None] | None = None,
    ) -> None:
        self._parent = parent
        self._manager = manager
        self._statuses = statuses
        self._recording = recording
        self._network = network
        self._on_property_grid = on_property_grid
        self.selected: str | None = None
        self.rows: dict[str, CameraRow] = {}

        with dpg.table(parent=parent, header_row=False, policy=dpg.mvTable_SizingFixedFit, borders_innerH=False,
                       borders_outerH=False, borders_innerV=False, borders_outerV=False) as head:
            dpg.add_table_column(width_stretch=True)
            dpg.add_table_column(width_fixed=True)
            with dpg.table_row():
                with dpg.group(horizontal=True, horizontal_spacing=10):
                    with theme.nudge(2):
                        title = dpg.add_text("Cameras")
                    self._count = dpg.add_button(label="0", height=24)
                self._scan = dpg.add_button(label=theme.ICON_REFRESH, width=30, height=28,
                                            callback=lambda: on_scan() if on_scan else None, show=on_scan is not None)
        bind(head, "tight")
        theme.use_font(title, "title")
        theme.use_font(self._count, "caption")
        theme.use_font(self._scan, "icon")
        theme.bind(self._count, "pill_neutral")
        theme.bind(self._scan, "icon_ghost")
        self._empty = secondary_text("No cameras found yet. Connect a camera: it appears here automatically.",
                                     parent=parent, wrap=300)
        self._add_new_rows()

    def update(self) -> None:
        if list(self.rows) != self._manager.camera_ids:
            self._sync_rows()
        for row in self.rows.values():
            row.update()

    def _sync_rows(self) -> None:
        present = set(self._manager.camera_ids)
        for camera_id in [cid for cid in self.rows if cid not in present]:
            self.rows.pop(camera_id).delete()
            if self.selected == camera_id:
                self.selected = None
        self._add_new_rows()

    def _add_new_rows(self) -> None:
        for camera_id in self._manager.camera_ids:
            if camera_id in self.rows:
                continue
            self.rows[camera_id] = CameraRow(
                self._parent, self._statuses.status(camera_id), self._manager, self._statuses, self._recording,
                self._network, on_select=self.select, on_property_grid=self._on_property_grid,
            )
        dpg.configure_item(self._count, label=str(len(self.rows)))
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
