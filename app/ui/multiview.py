"""Grid of CameraView tiles with selectable layouts.

The layout picker is a segmented control that the main window places in its toolbar
(``build_layout_control``), so the tiles get the full height of the stream area.
"""

from __future__ import annotations

import dearpygui.dearpygui as dpg

from app.cameras.camera_manager import CameraManager
from app.services.reconnect_service import ReconnectService
from app.services.recording_service import RecordingService
from app.ui.camera_view import CameraView
from app.ui.theme import SegmentedControl, bind

# name -> (columns, rows)
LAYOUTS: dict[str, tuple[int, int]] = {
    "1x1": (1, 1),
    "2x1": (2, 1),
    "2x2": (2, 2),
    "3x3": (3, 3),
    "4x4": (4, 4),
}
TILE_SPACING = 8


def layout_label(layout: str) -> str:
    return layout.replace("x", " × ")


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
        self._control: SegmentedControl | None = None
        self._grid = dpg.add_child_window(parent=parent, border=False, no_scrollbar=True, no_scroll_with_mouse=True)
        bind(self._grid, "canvas")
        self._build()

    def build_layout_control(self, parent: int | str) -> SegmentedControl:
        """Segmented layout picker (1 × 1 … 4 × 4), placed by the caller."""
        labels = {layout_label(name): name for name in LAYOUTS}
        self._control = SegmentedControl(parent, list(labels), layout_label(self._layout),
                                         lambda label: self.set_layout(labels[label]), segment_width=58)
        return self._control

    @property
    def layout(self) -> str:
        return self._layout

    def set_layout(self, layout: str) -> None:
        if layout == self._layout or layout not in LAYOUTS:
            return
        self._layout = layout
        if self._control is not None:
            self._control.set(layout_label(layout))
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
