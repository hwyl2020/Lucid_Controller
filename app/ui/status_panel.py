"""Collapsible "Camera Status" section: one line per camera with bandwidth (Mb/s), FPS and frames.

Values come from CameraStatusService and are refreshed at ``refresh_hz`` (not per rendered frame);
nothing is updated while the section is collapsed. The list grows with the number of cameras up to
``MAX_VISIBLE_ROWS`` and scrolls beyond that.
"""

from __future__ import annotations

import time

import dearpygui.dearpygui as dpg

from app.models.camera_state import CameraState
from app.models.camera_status import CameraStatus
from app.services.camera_status_service import CameraStatusService
from app.ui import theme
from app.ui.theme import STATE_COLORS, STATE_NAMES, bind, caption, secondary_text, use_font

ROW_HEIGHT = 29  # body font row in a compact table
HEADER_ROW_HEIGHT = 23
BODY_PADDING = 16  # top + bottom padding of the card
MAX_VISIBLE_ROWS = 6
# Fixed widths keep the figures next to the camera name; a trailing stretch column takes the rest.
COLUMNS = (("Camera", 300.0), ("Status", 120.0), ("Bandwidth", 150.0), ("FPS", 90.0), ("Frames", 120.0), ("", 0.0))
DASH = "—"


class _Line:
    def __init__(self, table: int | str, status: CameraStatus) -> None:
        with dpg.table_row(parent=table):
            name = dpg.add_text(status.display_name)
            with dpg.group(horizontal=True, horizontal_spacing=6):
                self.dot = dpg.add_text("●")
                self.state = dpg.add_text("")
            self.bandwidth = dpg.add_text(DASH)
            self.fps = dpg.add_text(DASH)
            self.frames = dpg.add_text(DASH)
            dpg.add_text("")  # filler cell for the stretch column
        use_font(name, "heading")
        use_font(self.dot, "caption")
        self._last: tuple | None = None

    def update(self, status: CameraStatus) -> None:
        streaming = status.state is CameraState.ACQUIRING
        shown = (
            status.state,
            f"{status.bandwidth_mbps:,.1f} Mb/s" if streaming else DASH,
            f"{status.fps:.2f}" if streaming else DASH,
            f"{status.frame_count:,}" if streaming else DASH,
            theme.revision(),
        )
        if shown == self._last:
            return  # avoid needless widget updates
        self._last = shown
        color = STATE_COLORS[status.state]
        dpg.configure_item(self.dot, color=color)
        dpg.set_value(self.state, STATE_NAMES[status.state])
        dpg.set_value(self.bandwidth, shown[1])
        dpg.set_value(self.fps, shown[2])
        dpg.set_value(self.frames, shown[3])

    def values(self) -> dict[str, str]:
        return {
            "Status": dpg.get_value(self.state),
            "Bandwidth": dpg.get_value(self.bandwidth),
            "FPS": dpg.get_value(self.fps),
            "Frames": dpg.get_value(self.frames),
        }


class StatusPanel:
    def __init__(self, parent: int | str, statuses: CameraStatusService, refresh_hz: float, default_open: bool = True) -> None:
        self._statuses = statuses
        self._interval = 1.0 / max(refresh_hz, 0.5)
        self._last_refresh = 0.0
        self._lines: dict[str, _Line] = {}
        self._camera_ids: tuple[str, ...] | None = None

        with dpg.group(parent=parent) as self.group:
            self.header = dpg.add_collapsing_header(label="Camera Status", default_open=default_open)
            use_font(self.header, "heading")
            with dpg.child_window(parent=self.header, height=HEADER_ROW_HEIGHT + ROW_HEIGHT + BODY_PADDING,
                                  border=True) as self._body:
                with dpg.group() as stack:  # no gap between the column captions and the rows
                    with dpg.group() as self._columns_group:
                        with dpg.table(header_row=False, borders_innerH=False, borders_outerH=False,
                                       borders_innerV=False, borders_outerV=False,
                                       policy=dpg.mvTable_SizingStretchProp) as self._columns:
                            for label, width in COLUMNS:
                                if width:
                                    dpg.add_table_column(width_fixed=True, init_width_or_weight=width)
                                else:
                                    dpg.add_table_column(width_stretch=True)
                            with dpg.table_row():
                                for label, _ in COLUMNS:
                                    caption(label) if label else dpg.add_text("")
                    with dpg.group() as self._table_group:
                        with dpg.table(header_row=False, row_background=True, borders_innerH=False, borders_outerH=False,
                                       borders_innerV=False, borders_outerV=False,
                                       policy=dpg.mvTable_SizingStretchProp) as self._table:
                            for label, width in COLUMNS:
                                if width:
                                    dpg.add_table_column(width_fixed=True, init_width_or_weight=width)
                                else:
                                    dpg.add_table_column(width_stretch=True)
                self._empty = secondary_text("No cameras", show=False)
        bind(self._body, "surface")
        bind(stack, "stack")
        bind(self._columns, "compact_table")
        bind(self._table, "compact_table")
        self._fit_pending = False

    @property
    def is_open(self) -> bool:
        return bool(dpg.get_value(self.header))

    def set_open(self, open_: bool) -> None:
        dpg.set_value(self.header, open_)

    def line(self, camera_id: str) -> _Line:
        return self._lines[camera_id]

    @property
    def line_count(self) -> int:
        return len(self._lines)

    def update(self) -> None:
        if not self.is_open:
            return
        statuses = self._statuses.statuses()
        camera_ids = tuple(s.camera_id for s in statuses)
        if camera_ids != self._camera_ids:
            self._build(statuses)
            self._camera_ids = camera_ids
            self._last_refresh = 0.0
        if self._fit_pending:
            self._fit_height()
        now = time.monotonic()
        if now - self._last_refresh < self._interval:
            return
        self._last_refresh = now
        for status in statuses:
            line = self._lines.get(status.camera_id)
            if line is not None:
                line.update(status)

    def _build(self, statuses: list[CameraStatus]) -> None:
        dpg.delete_item(self._table, children_only=True, slot=1)  # rows only; keep the columns
        self._lines = {status.camera_id: _Line(self._table, status) for status in statuses}
        dpg.configure_item(self._empty, show=not statuses)
        visible = min(max(len(statuses), 1), MAX_VISIBLE_ROWS)
        dpg.configure_item(self._body, height=HEADER_ROW_HEIGHT + visible * ROW_HEIGHT + BODY_PADDING + 8)
        self._fit_pending = bool(statuses)

    def _fit_height(self) -> None:
        """Size the card to its rows once they have been laid out (exact for any font size)."""
        header_h = dpg.get_item_rect_size(self._columns_group)[1]
        table_h = dpg.get_item_rect_size(self._table_group)[1]
        rows = len(self._lines)
        if header_h <= 0 or table_h <= 0 or not rows:
            return
        self._fit_pending = False
        visible = min(rows, MAX_VISIBLE_ROWS)
        height = header_h + table_h * visible / rows + 2 * 12
        dpg.configure_item(self._body, height=int(round(height)))
