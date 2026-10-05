"""Collapsible "Logs" section: time / level / camera / message rows from the in-memory LogBuffer.

Each row shows the camera the record concerns (``Model (Serial)``; "System" for application-wide
records), from the ``camera_id`` tag set by ``app.camera_log``. Controls: level filter
(All | Debug | Info | Warning | Error), camera filter, auto-scroll, clear. New entries are
pulled from the buffer at ``refresh_hz``; logging threads never wait on the UI. The table uses a
clipper so only visible rows are drawn, and keeps at most ``MAX_ROWS`` rows.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import dearpygui.dearpygui as dpg

from app.services.log_buffer import LogBuffer, LogEntry
from app.ui import theme as ui_theme
from app.ui.theme import COLORS, THEME_TEXT, SegmentedControl, bind, secondary_text, use_font

MAX_ROWS = 1000
PANEL_HEIGHT = 180
FILTERS = ("All", "Debug", "Info", "Warning", "Error")
ALL_CAMERAS = "All cameras"
SYSTEM = "System (no camera)"
CAMERA_COLUMN_WIDTH = 200
LEVEL_TOKENS = {
    logging.DEBUG: "text_tertiary",
    logging.INFO: "accent_text",
    logging.WARNING: "warning",
    logging.ERROR: "error",
    logging.CRITICAL: "error",
}


def level_color(levelno: int) -> tuple:
    """Current-theme colour for a log level badge."""
    return COLORS[LEVEL_TOKENS.get(levelno, "text_secondary")]


def matches(entry: LogEntry, level_filter: str) -> bool:
    """Exact-level filter; "Error" also includes CRITICAL; "All" shows everything."""
    if level_filter == "All":
        return True
    if level_filter == "Error":
        return entry.levelno >= logging.ERROR
    return entry.level == level_filter.upper()


def matches_camera(entry: LogEntry, camera_filter: str | None) -> bool:
    """``None`` = all cameras, ``""`` = application-wide records only, else that camera's id."""
    if camera_filter is None:
        return True
    if camera_filter == "":
        return entry.camera_id is None
    return entry.camera_id == camera_filter


class LogPanel:
    def __init__(
        self,
        parent: int | str,
        buffer: LogBuffer,
        refresh_hz: float,
        default_open: bool = False,
        theme: str = "dark",
        camera_names: Callable[[], dict[str, str]] = dict,
    ) -> None:
        self._buffer = buffer
        self._camera_names = camera_names  # camera_id -> "Model (Serial)"
        self._names: dict[str, str] = {}
        self._camera_filter: str | None = None
        self._interval = 1.0 / max(refresh_hz, 0.5)
        self._last_refresh = 0.0
        self._last_seq = 0
        self._rows: list[int | str] = []
        self._dirty = True  # rebuild from the buffer (first show, filter change, reopened)
        self._level_filter = "All"
        self._revision = ui_theme.revision()

        with dpg.group(parent=parent) as self.group:
            self.header = dpg.add_collapsing_header(label="Logs", default_open=default_open)
            use_font(self.header, "heading")
            with dpg.group(horizontal=True, parent=self.header):
                self._levels = SegmentedControl(None, FILTERS, self._level_filter, self.set_filter, segment_width=70)
                dpg.add_spacer(width=4)
                self._camera_combo = dpg.add_combo([ALL_CAMERAS, SYSTEM], default_value=ALL_CAMERAS, width=240,
                                                   callback=lambda _s, label: self._on_camera_filter(label))
                dpg.add_spacer(width=4)
                self._autoscroll = dpg.add_checkbox(label="Auto-scroll", default_value=True)
                dpg.add_spacer(width=4)
                dpg.add_button(label="Clear", width=72, callback=self.clear)
                dpg.add_spacer(width=4)
                self._count = secondary_text("")
            self._hint = dpg.add_text("", color=COLORS["warning"], parent=self.header, show=False)
            with dpg.child_window(parent=self.header, height=PANEL_HEIGHT, border=True) as self._body:
                with dpg.table(header_row=False, clipper=True, row_background=True,
                               borders_innerH=False, borders_outerH=False, borders_innerV=False,
                               borders_outerV=False, policy=dpg.mvTable_SizingFixedFit) as self._table:
                    dpg.add_table_column(width_fixed=True, init_width_or_weight=72)
                    dpg.add_table_column(width_fixed=True, init_width_or_weight=70)
                    dpg.add_table_column(width_fixed=True, init_width_or_weight=CAMERA_COLUMN_WIDTH)
                    dpg.add_table_column(width_stretch=True)
            bind(self._body, "surface")
            bind(self._table, "compact_table")
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

    @property
    def camera_filter(self) -> str | None:
        return self._camera_filter

    def set_camera_filter(self, camera_id: str | None) -> None:
        """``None`` = all cameras, ``""`` = application-wide only, else one camera id."""
        self._refresh_camera_names()
        self._camera_filter = camera_id
        label = ALL_CAMERAS if camera_id is None else SYSTEM if camera_id == "" else self._names.get(camera_id, camera_id)
        dpg.set_value(self._camera_combo, label)
        self._mark_dirty()

    def _on_camera_filter(self, label: str) -> None:
        by_label = {name: cid for cid, name in self._names.items()}
        self.set_camera_filter(None if label == ALL_CAMERAS else "" if label == SYSTEM else by_label.get(label))

    def _refresh_camera_names(self) -> None:
        names = dict(self._camera_names())
        if names != self._names:
            self._names = names
            dpg.configure_item(self._camera_combo, items=[ALL_CAMERAS, SYSTEM, *names.values()])

    def set_theme(self, theme_name: str) -> None:
        """Row colours are baked into the rows: rebuild them in the new palette."""
        self._mark_dirty()

    def _show_selected_segment(self) -> None:
        if hasattr(self, "_levels"):
            self._levels.set(self._level_filter)

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
        if ui_theme.revision() != self._revision:
            self._revision = ui_theme.revision()
            self._dirty = True
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
        self._refresh_camera_names()
        if self._dirty:
            self._delete_rows()
            entries = self._buffer.entries()
            self._dirty = False
            self._update_hint(level_filter)
        else:
            entries = self._buffer.since(self._last_seq)
        if entries:
            self._last_seq = entries[-1].seq
            new = [e for e in entries if matches(e, level_filter) and matches_camera(e, self._camera_filter)]
            new = new[-MAX_ROWS:]
            for entry in new:
                self._add_row(entry)
            overflow = len(self._rows) - MAX_ROWS
            if overflow > 0:
                for row in self._rows[:overflow]:
                    dpg.delete_item(row)
                del self._rows[:overflow]
        self._update_count()

    def _add_row(self, entry: LogEntry) -> None:
        color = level_color(entry.levelno)
        error = entry.levelno >= logging.ERROR
        with dpg.table_row(parent=self._table) as row:
            stamp = time.strftime("%H:%M:%S", time.localtime(entry.timestamp))
            camera = self._names.get(entry.camera_id, entry.camera_id) if entry.camera_id else "System"
            for text, text_color, font in ((stamp, COLORS["text_tertiary"], "mono"),
                                           (entry.level, color, "caption"),
                                           (camera, THEME_TEXT if entry.camera_id else COLORS["text_secondary"], "small"),
                                           (entry.message, color if error else THEME_TEXT, "mono")):
                item = dpg.add_text(text, color=text_color)
                use_font(item, font)
        self._rows.append(row)

    def _delete_rows(self) -> None:
        dpg.delete_item(self._table, children_only=True, slot=1)
        self._rows = []

    def _mark_dirty(self) -> None:
        self._dirty = True

    def _update_count(self) -> None:
        dpg.set_value(self._count, f"{len(self._rows):,} entries")

    def _update_hint(self, level_filter: str) -> None:
        capture = logging.getLogger().getEffectiveLevel()
        hidden = level_filter == "Debug" and capture > logging.DEBUG
        dpg.set_value(self._hint, "Debug messages are not being captured. Set Log level to DEBUG in File ▸ Settings.")
        dpg.configure_item(self._hint, show=hidden)
