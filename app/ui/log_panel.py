"""Collapsible "Logs" section: timestamp / level / message rows from the in-memory LogBuffer.

Controls: level filter (All | Debug | Info | Warning | Error), auto-scroll, clear. New entries are
pulled from the buffer at ``refresh_hz``; logging threads never wait on the UI. The table uses a
clipper so only visible rows are drawn, and keeps at most ``MAX_ROWS`` rows.
"""

from __future__ import annotations

import logging
import time

import dearpygui.dearpygui as dpg

from app.models.camera_state import CameraState
from app.services.log_buffer import LogBuffer, LogEntry
from app.ui.theme import (
    STATE_COLORS,
    TEXT_DIM,
    THEME_TEXT,
    WARNING_COLOR,
    compact_table_theme,
    segment_selected_theme,
    use_font,
)

MAX_ROWS = 1000
PANEL_HEIGHT = 170
FILTERS = ("All", "Debug", "Info", "Warning", "Error")
LEVEL_COLORS = {
    logging.DEBUG: TEXT_DIM,
    logging.INFO: THEME_TEXT,
    logging.WARNING: WARNING_COLOR,
    logging.ERROR: STATE_COLORS[CameraState.ERROR],
    logging.CRITICAL: STATE_COLORS[CameraState.ERROR],
}


def matches(entry: LogEntry, level_filter: str) -> bool:
    """Exact-level filter; "Error" also includes CRITICAL; "All" shows everything."""
    if level_filter == "All":
        return True
    if level_filter == "Error":
        return entry.levelno >= logging.ERROR
    return entry.level == level_filter.upper()


class LogPanel:
    def __init__(
        self, parent: int | str, buffer: LogBuffer, refresh_hz: float, default_open: bool = False, theme: str = "dark"
    ) -> None:
        self._buffer = buffer
        self._interval = 1.0 / max(refresh_hz, 0.5)
        self._last_refresh = 0.0
        self._last_seq = 0
        self._rows: list[int | str] = []
        self._dirty = True  # rebuild from the buffer (first show, filter change, reopened)
        self._level_filter = "All"
        self._segment_theme = segment_selected_theme(theme)
        self._segments: dict[str, int | str] = {}

        with dpg.group(parent=parent) as self.group:
            self.header = dpg.add_collapsing_header(label="Logs", default_open=default_open)
            use_font(self.header, "heading")
            with dpg.group(horizontal=True, parent=self.header):
                with dpg.group(horizontal=True, horizontal_spacing=2):  # segmented control
                    for name in FILTERS:
                        self._segments[name] = dpg.add_button(
                            label=name, width=72, callback=lambda _s, _a, n: self.set_filter(n), user_data=name
                        )
                dpg.add_spacer(width=12)
                self._autoscroll = dpg.add_checkbox(label="Auto-scroll", default_value=True)
                dpg.add_spacer(width=4)
                dpg.add_button(label="Clear", width=70, callback=self.clear)
                dpg.add_spacer(width=8)
                self._count = dpg.add_text("", color=TEXT_DIM)
            self._hint = dpg.add_text("", color=TEXT_DIM, parent=self.header, show=False)
            with dpg.child_window(parent=self.header, height=PANEL_HEIGHT, border=True) as self._body:
                with dpg.table(header_row=False, clipper=True, row_background=True,
                               borders_innerH=False, borders_outerH=False, borders_innerV=False,
                               borders_outerV=False, policy=dpg.mvTable_SizingFixedFit) as self._table:
                    dpg.add_table_column(width_fixed=True, init_width_or_weight=78)
                    dpg.add_table_column(width_fixed=True, init_width_or_weight=78)
                    dpg.add_table_column(width_stretch=True)
            dpg.bind_item_theme(self._table, compact_table_theme())
        self._was_open = self.is_open
        self._show_selected_segment()

    @property
    def is_open(self) -> bool:
        return bool(dpg.get_value(self.header))

    def set_open(self, open_: bool) -> None:
        dpg.set_value(self.header, open_)

    @property
    def level_filter(self) -> str:
        return self._level_filter

    def set_filter(self, level_filter: str) -> None:
        if level_filter not in FILTERS:
            raise ValueError(f"Unknown log filter {level_filter!r}")
        self._level_filter = level_filter
        self._show_selected_segment()
        self._mark_dirty()

    def set_theme(self, theme: str) -> None:
        self._segment_theme = segment_selected_theme(theme)
        self._show_selected_segment()

    def _show_selected_segment(self) -> None:
        for name, button in self._segments.items():
            dpg.bind_item_theme(button, self._segment_theme if name == self._level_filter else 0)

    @property
    def row_count(self) -> int:
        return len(self._rows)

    def clear(self) -> None:
        self._buffer.clear()
        self._delete_rows()
        self._update_count()

    def update(self) -> None:
        is_open = self.is_open
        if is_open and not self._was_open:
            self._dirty = True  # rows were not maintained while collapsed
        self._was_open = is_open
        if not is_open:
            return
        if dpg.get_value(self._autoscroll):
            # Pin to the bottom every frame: scroll max only updates after rows are rendered, so a
            # one-shot scroll can land short (e.g. right after a filter rebuild).
            bottom = dpg.get_y_scroll_max(self._body)
            if dpg.get_y_scroll(self._body) < bottom:
                dpg.set_y_scroll(self._body, bottom)
        now = time.monotonic()
        if not self._dirty and now - self._last_refresh < self._interval:
            return
        self._last_refresh = now

        level_filter = self.level_filter
        if self._dirty:
            self._delete_rows()
            entries = self._buffer.entries()
            self._dirty = False
            self._update_hint(level_filter)
        else:
            entries = self._buffer.since(self._last_seq)
        if entries:
            self._last_seq = entries[-1].seq
            new = [e for e in entries if matches(e, level_filter)][-MAX_ROWS:]
            for entry in new:
                self._add_row(entry)
            overflow = len(self._rows) - MAX_ROWS
            if overflow > 0:
                for row in self._rows[:overflow]:
                    dpg.delete_item(row)
                del self._rows[:overflow]
        self._update_count()

    def _add_row(self, entry: LogEntry) -> None:
        color = LEVEL_COLORS.get(entry.levelno, THEME_TEXT)
        with dpg.table_row(parent=self._table) as row:
            stamp = time.strftime("%H:%M:%S", time.localtime(entry.timestamp))
            for text, text_color in ((stamp, TEXT_DIM), (entry.level, color), (entry.message, THEME_TEXT)):
                item = dpg.add_text(text, color=text_color)
                use_font(item, "mono")
            if entry.levelno >= logging.ERROR:
                dpg.configure_item(item, color=color)
        self._rows.append(row)

    def _delete_rows(self) -> None:
        dpg.delete_item(self._table, children_only=True, slot=1)
        self._rows = []

    def _mark_dirty(self) -> None:
        self._dirty = True

    def _update_count(self) -> None:
        dpg.set_value(self._count, f"{len(self._rows):,} shown")

    def _update_hint(self, level_filter: str) -> None:
        capture = logging.getLogger().getEffectiveLevel()
        hidden = level_filter == "Debug" and capture > logging.DEBUG
        dpg.set_value(self._hint, "Debug messages are not being captured. Set Log level to DEBUG in File ▸ Settings.")
        dpg.configure_item(self._hint, show=hidden)
