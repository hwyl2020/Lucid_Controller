"""Collapsible "Camera Status" section: one card per camera with bandwidth (Mb/s), FPS and frames.

Values come from CameraStatusService and are refreshed at ``refresh_hz`` (not per rendered frame);
nothing is updated while the section is collapsed.
"""

from __future__ import annotations

import time

import dearpygui.dearpygui as dpg

from app.models.camera_state import CameraState
from app.models.camera_status import CameraStatus
from app.services.camera_status_service import CameraStatusService
from app.ui.theme import STATE_COLORS, TEXT_DIM, compact_table_theme, use_font

CARD_WIDTH = 260
CARD_HEIGHT = 136
CARD_SPACING = 8
LABEL_COLUMN = 88
MAX_VISIBLE_ROWS = 2
DASH = "—"


_COMPACT: int | None = None


def _compact_theme() -> int:
    global _COMPACT
    if _COMPACT is None or not dpg.does_item_exist(_COMPACT):
        _COMPACT = compact_table_theme()
    return _COMPACT


class _Card:
    def __init__(self, parent: int | str, status: CameraStatus) -> None:
        self.camera_id = status.camera_id
        with dpg.child_window(parent=parent, width=CARD_WIDTH, height=CARD_HEIGHT, border=True,
                              no_scrollbar=True) as self.window:
            with dpg.group(horizontal=True, horizontal_spacing=6):
                self.dot = dpg.add_text("●")
                title = dpg.add_text(status.display_name)
                use_font(title, "heading")
            dpg.add_spacer(height=2)
            with dpg.table(header_row=False, borders_innerH=False, borders_outerH=False,
                           borders_innerV=False, borders_outerV=False,
                           policy=dpg.mvTable_SizingFixedFit) as table:
                dpg.add_table_column(width_fixed=True, init_width_or_weight=LABEL_COLUMN)
                dpg.add_table_column(width_stretch=True)
                self.values = {}
                for label in ("Bandwidth", "FPS", "Frames"):
                    with dpg.table_row():
                        dpg.add_text(label, color=TEXT_DIM)
                        self.values[label] = dpg.add_text(DASH)
            dpg.bind_item_theme(table, _compact_theme())
        self._last: tuple | None = None

    def update(self, status: CameraStatus) -> None:
        streaming = status.state is CameraState.ACQUIRING
        shown = (
            status.state,
            f"{status.bandwidth_mbps:,.1f} Mb/s" if streaming else DASH,
            f"{status.fps:.2f}" if streaming else DASH,
            f"{status.frame_count:,}" if streaming else DASH,
        )
        if shown == self._last:
            return  # avoid needless widget updates
        self._last = shown
        dpg.configure_item(self.dot, color=STATE_COLORS[status.state])
        for label, text in zip(("Bandwidth", "FPS", "Frames"), shown[1:]):
            dpg.set_value(self.values[label], text)


class StatusPanel:
    def __init__(self, parent: int | str, statuses: CameraStatusService, refresh_hz: float, default_open: bool = True) -> None:
        self._statuses = statuses
        self._interval = 1.0 / max(refresh_hz, 0.5)
        self._last_refresh = 0.0
        self._cards: dict[str, _Card] = {}
        self._layout_key: tuple[int, int] | None = None
        self._rows: list[int | str] = []

        with dpg.group(parent=parent) as self.group:
            self.header = dpg.add_collapsing_header(label="Camera Status", default_open=default_open)
            use_font(self.header, "heading")
            with dpg.child_window(parent=self.header, height=CARD_HEIGHT + 16, border=False) as self._body:
                pass
            if not statuses.statuses():
                dpg.add_text("No cameras", color=TEXT_DIM, parent=self._body)

    @property
    def is_open(self) -> bool:
        return bool(dpg.get_value(self.header))

    def set_open(self, open_: bool) -> None:
        dpg.set_value(self.header, open_)

    def update(self) -> None:
        if not self.is_open:
            return
        width = dpg.get_item_rect_size(self._body)[0]
        statuses = self._statuses.statuses()
        if not statuses or width <= 0:
            return
        per_row = max(1, int((width + CARD_SPACING) // (CARD_WIDTH + CARD_SPACING)))
        key = (len(statuses), per_row)
        if key != self._layout_key:
            self._build(statuses, per_row)
            self._layout_key = key
            self._last_refresh = 0.0
        now = time.monotonic()
        if now - self._last_refresh < self._interval:
            return
        self._last_refresh = now
        for status in statuses:
            card = self._cards.get(status.camera_id)
            if card is not None:
                card.update(status)

    def _build(self, statuses: list[CameraStatus], per_row: int) -> None:
        for row in self._rows:
            dpg.delete_item(row)
        self._rows, self._cards = [], {}
        for start in range(0, len(statuses), per_row):
            row = dpg.add_group(horizontal=True, horizontal_spacing=CARD_SPACING, parent=self._body)
            self._rows.append(row)
            for status in statuses[start:start + per_row]:
                self._cards[status.camera_id] = _Card(row, status)
        rows = len(self._rows)
        visible = min(rows, MAX_VISIBLE_ROWS)
        height = visible * CARD_HEIGHT + (visible - 1) * CARD_SPACING + 16
        dpg.configure_item(self._body, height=height)
