"""Settings window: edits config.json and applies changes live where possible.

Grouped form: each section is a caption over a card of label/control rows. Folder fields show the
full path and have a Browse… button (native folder dialog, ``folder_picker``).
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

import dearpygui.dearpygui as dpg

from app.recording.recorder import RecordingMode
from app.services.app_services import AppServices
from app.services.configuration import save_config
from app.ui.folder_picker import FolderPicker
from app.ui.multiview import LAYOUTS
from app.ui.theme import ACCENTS, COLORS, DEFAULT_ACCENT, bind, caption, nudge, secondary_text, use_font

logger = logging.getLogger(__name__)

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
LABEL_WIDTH = 190
FIELD_WIDTH = 300
BROWSE_WIDTH = 92


class SettingsWindow:
    def __init__(self, services: AppServices, on_theme: Callable[[str], None],
                 on_accent: Callable[[str], None] | None = None, picker: FolderPicker | None = None) -> None:
        self._services = services
        self._picker = picker or FolderPicker()
        self._on_theme = on_theme
        self._on_accent = on_accent
        with dpg.window(label="Settings", width=680, height=660, show=False, pos=(300, 70), no_collapse=True) as self.window:
            with self._section("Appearance"):
                self._theme = self._row("Theme", lambda: dpg.add_combo(["dark", "light"], width=FIELD_WIDTH))
                self._accent = self._row("Accent colour", lambda: dpg.add_combo(list(ACCENTS), width=FIELD_WIDTH))
                self._layout = self._row("Default layout", lambda: dpg.add_combo(list(LAYOUTS), width=FIELD_WIDTH))
            with self._section("Recording"):
                self._rec_dir = self._row("Recordings folder", lambda: self._folder_field("Recordings folder"))
                self._rec_mode = self._row("Default mode", lambda: dpg.add_combo([m.value for m in RecordingMode],
                                                                                  width=FIELD_WIDTH))
                self._queue = self._row("Queue (frames/camera)", lambda: dpg.add_input_int(
                    width=FIELD_WIDTH, min_value=4, min_clamped=True))
                self._metadata = self._row("Recording info files", lambda: dpg.add_checkbox(
                    label="Save timestamps and session info"))
                with dpg.tooltip(self._metadata):
                    dpg.add_text("On: each recording also gets frames.csv (camera frame number and arrival time\n"
                                 "of every frame) and session.json (camera settings, start/stop, frame counts).\n"
                                 "Off: video recordings are just the video file. Raw recordings always keep\n"
                                 "frames.csv, which is needed to read frames.raw back.")
                self._min_free = self._row("Stop below free GB", lambda: dpg.add_input_float(
                    width=FIELD_WIDTH, min_value=0, min_clamped=True, format="%.1f"))
                self._snap_dir = self._row("Images folder", lambda: self._folder_field("Images (capture) folder"))
            with self._section("Cameras & logging"):
                self._reconnect = self._row("Auto-reconnect", lambda: dpg.add_checkbox(label="Reconnect lost cameras"))
                self._log_level = self._row("Log level", lambda: dpg.add_combo(LOG_LEVELS, width=FIELD_WIDTH))
            dpg.add_spacer(height=4)
            with dpg.group(horizontal=True):
                save = dpg.add_button(label="Save", width=96, callback=self._save)
                dpg.add_button(label="Close", width=96, callback=lambda: dpg.hide_item(self.window))
                self._message = dpg.add_text("")
            bind(save, "primary")
            path = secondary_text(f"Saved to {Path(services.config_path).resolve()}", wrap=630)
            use_font(path, "small")

    # --- form helpers ------------------------------------------------------------
    @staticmethod
    @contextmanager
    def _section(title: str):
        """Caption over a card holding a two-column (label | control) table."""
        caption(title)
        with dpg.child_window(auto_resize_y=True, no_scrollbar=True, border=True) as card:
            with dpg.table(header_row=False, policy=dpg.mvTable_SizingFixedFit, borders_innerH=True,
                           borders_outerH=False, borders_innerV=False, borders_outerV=False) as table:
                dpg.add_table_column(width_fixed=True, init_width_or_weight=LABEL_WIDTH)
                dpg.add_table_column(width_stretch=True)
                yield
        bind(card, "surface")
        bind(table, "compact_table")
        dpg.add_spacer(height=4)

    def _folder_field(self, title: str) -> int | str:
        """Path text box + Browse… (native folder dialog); returns the text box."""
        with dpg.group(horizontal=True):
            field = dpg.add_input_text(width=-(BROWSE_WIDTH + 8))  # the path gets all remaining width
            button = dpg.add_button(label="Browse…", width=BROWSE_WIDTH,
                                    callback=lambda: self._browse(field, title))
        with dpg.tooltip(button):
            dpg.add_text("Choose the folder")
        return field

    def _browse(self, field: int | str, title: str) -> None:
        self._picker.choose(f"Select the {title.lower()}", dpg.get_value(field).strip(),
                            lambda path: dpg.set_value(field, path))

    def update(self) -> None:
        """Called every UI frame: hands a chosen folder back to its field."""
        self._picker.poll()

    @staticmethod
    def _row(label: str, make):
        with dpg.table_row():
            with nudge(0):
                dpg.add_text(label)
            return make()

    # --- behaviour ----------------------------------------------------------------
    def show(self) -> None:
        cfg = self._services.config
        dpg.set_value(self._theme, cfg["application"]["theme"])
        dpg.set_value(self._accent, cfg["application"].get("accent", DEFAULT_ACCENT))
        dpg.set_value(self._layout, cfg["application"]["default_layout"])
        dpg.set_value(self._rec_dir, str(Path(cfg["recording"]["directory"]).resolve()))
        dpg.set_value(self._rec_mode, cfg["recording"]["mode"])
        dpg.set_value(self._queue, int(cfg["recording"]["queue_frames"]))
        dpg.set_value(self._min_free, float(cfg["recording"]["min_free_gb"]))
        dpg.set_value(self._metadata, bool(cfg["recording"].get("save_metadata", False)))
        dpg.set_value(self._snap_dir, str(Path(cfg["snapshots"]["directory"]).resolve()))
        dpg.set_value(self._reconnect, bool(cfg["reconnect"]["enabled"]))
        dpg.set_value(self._log_level, cfg["logging"]["level"])
        dpg.set_value(self._message, "")
        dpg.show_item(self.window)
        dpg.focus_item(self.window)

    def _save(self) -> None:
        services = self._services
        new = copy.deepcopy(services.config)
        new["application"]["theme"] = dpg.get_value(self._theme)
        new["application"]["accent"] = dpg.get_value(self._accent) or DEFAULT_ACCENT
        new["application"]["default_layout"] = dpg.get_value(self._layout)
        new["recording"]["directory"] = dpg.get_value(self._rec_dir).strip() or "recordings"
        new["recording"]["mode"] = dpg.get_value(self._rec_mode)
        new["recording"]["queue_frames"] = max(4, int(dpg.get_value(self._queue)))
        new["recording"]["min_free_gb"] = max(0.0, float(dpg.get_value(self._min_free)))
        new["recording"]["save_metadata"] = bool(dpg.get_value(self._metadata))
        new["snapshots"]["directory"] = dpg.get_value(self._snap_dir).strip() or "snapshots"
        new["reconnect"]["enabled"] = bool(dpg.get_value(self._reconnect))
        new["logging"]["level"] = dpg.get_value(self._log_level)

        try:
            save_config(new, services.config_path)
        except OSError as exc:
            self._show(f"Could not save settings: {exc}", error=True)
            return
        services.config.clear()
        services.config.update(new)

        self._on_theme(new["application"]["theme"])
        if self._on_accent is not None:
            self._on_accent(new["application"]["accent"])
        logging.getLogger().setLevel(new["logging"]["level"])
        services.reconnect.enabled = new["reconnect"]["enabled"]
        if services.recording.active:
            note = " Recording settings apply to the next recording."
        else:
            note = ""
        services.recording.reconfigure(new)
        logger.info("Settings saved to %s", services.config_path)
        self._show("Saved." + note)

    def _show(self, text: str, error: bool = False) -> None:
        dpg.set_value(self._message, text)
        dpg.configure_item(self._message, color=COLORS["error"] if error else COLORS["success"])
