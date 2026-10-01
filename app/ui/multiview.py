"""Grid of CameraView tiles with selectable layouts."""

from __future__ import annotations

import dearpygui.dearpygui as dpg

from app.cameras.camera_manager import CameraManager
from app.services.reconnect_service import ReconnectService
from app.services.recording_service import RecordingService
from app.ui.camera_view import CameraView
from app.ui.theme import TEXT_DIM

# name -> (columns, rows)
LAYOUTS: dict[str, tuple[int, int]] = {
    "1x1": (1, 1),
    "2x1": (2, 1),
    "2x2": (2, 2),
    "3x3": (3, 3),
    "4x4": (4, 4),
}
TILE_SPACING = 6


class MultiView:
    def __init__(
        self,
        parent: int | str,
        manager: CameraManager,
        layout: str = "2x2",
        recording: RecordingService | None = None,
        reconnect: ReconnectService | None = None,
    ) -> None:
        self._manager = manager
        self._recording = recording
        self._reconnect = reconnect
        self._views: list[CameraView] = []
        self._rows: list[int | str] = []
        self._grid_size = (0, 0)
        self._layout = layout if layout in LAYOUTS else "2x2"
        self._texture_registry = dpg.add_texture_registry()

        with dpg.group(horizontal=True, parent=parent):
            dpg.add_text("MULTIVIEW", color=TEXT_DIM)
            dpg.add_spacer(width=12)
            self._layout_combo = dpg.add_combo(
                list(LAYOUTS),
                default_value=self._layout,
                width=80,
                callback=lambda _s, value: self.set_layout(value),
            )
        self._grid = dpg.add_child_window(parent=parent, border=False, no_scrollbar=True)
        self._build()

    @property
    def layout(self) -> str:
        return self._layout

    def set_layout(self, layout: str) -> None:
        if layout == self._layout or layout not in LAYOUTS:
            return
        self._layout = layout
        dpg.set_value(self._layout_combo, layout)
        self._build()

    def display_fps(self, camera_id: str) -> float:
        return next((v.display_fps for v in self._views if v.camera_id == camera_id), 0.0)

    def update(self) -> None:
        width, height = dpg.get_item_rect_size(self._grid)
        if (width, height) != self._grid_size and width > 0 and height > 0:
            self._grid_size = (width, height)
            self._resize_tiles()
        for view in self._views:
            view.update(self._manager, self._recording, self._reconnect)

    def _build(self) -> None:
        for view in self._views:
            view.delete()
        for row in self._rows:
            dpg.delete_item(row)
        self._views, self._rows = [], []

        columns, rows = LAYOUTS[self._layout]
        camera_ids = self._manager.camera_ids
        for _ in range(rows):
            row = dpg.add_group(horizontal=True, horizontal_spacing=TILE_SPACING, parent=self._grid)
            self._rows.append(row)
            for _ in range(columns):
                view = CameraView(row, self._texture_registry)
                index = len(self._views)
                view.assign(camera_ids[index] if index < len(camera_ids) else None, self._manager)
                self._views.append(view)
        self._resize_tiles()

    def _resize_tiles(self) -> None:
        width, height = self._grid_size
        if width <= 0 or height <= 0:
            return
        columns, rows = LAYOUTS[self._layout]
        tile_w = (width - TILE_SPACING * (columns - 1)) // columns
        tile_h = (height - TILE_SPACING * (rows - 1)) // rows
        for view in self._views:
            view.set_size(max(tile_w, 40), max(tile_h, 40))
