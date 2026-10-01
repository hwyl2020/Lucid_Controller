"""Settings window: edits config.json and applies changes live where possible."""

from __future__ import annotations

import copy
import logging
from collections.abc import Callable

import dearpygui.dearpygui as dpg

from app.models.camera_state import CameraState
from app.recording.recorder import RecordingMode
from app.services.app_services import AppServices
from app.services.configuration import save_config
from app.ui.multiview import LAYOUTS
from app.ui.theme import STATE_COLORS, TEXT_DIM

logger = logging.getLogger(__name__)

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


class SettingsWindow:
    def __init__(self, services: AppServices, on_theme: Callable[[str], None]) -> None:
        self._services = services
        self._on_theme = on_theme
        with dpg.window(label="Settings", width=520, height=560, show=False, pos=(300, 80), no_collapse=True) as self.window:
            dpg.add_text("APPEARANCE", color=TEXT_DIM)
            self._theme = dpg.add_combo(["dark", "light"], label="Theme", width=200)
            self._layout = dpg.add_combo(list(LAYOUTS), label="Default layout", width=200)
            dpg.add_spacer(height=6)
            dpg.add_text("RECORDING", color=TEXT_DIM)
            self._rec_dir = dpg.add_input_text(label="Recordings folder", width=300)
            self._rec_mode = dpg.add_combo([m.value for m in RecordingMode], label="Default mode", width=200)
            self._queue = dpg.add_input_int(label="Queue (frames/camera)", width=200, min_value=4, min_clamped=True)
            self._min_free = dpg.add_input_float(label="Stop below free GB", width=200, min_value=0, min_clamped=True, format="%.1f")
            self._snap_dir = dpg.add_input_text(label="Snapshots folder", width=300)
            dpg.add_spacer(height=6)
            dpg.add_text("CAMERAS & LOGGING", color=TEXT_DIM)
            self._reconnect = dpg.add_checkbox(label="Automatically reconnect lost cameras")
            self._log_level = dpg.add_combo(LOG_LEVELS, label="Log level", width=200)
            dpg.add_spacer(height=10)
            with dpg.group(horizontal=True):
                dpg.add_button(label="Save", width=90, callback=self._save)
                dpg.add_button(label="Close", width=90, callback=lambda: dpg.hide_item(self.window))
            self._message = dpg.add_text("", wrap=490)
            dpg.add_text(f"Saved to {services.config_path}", color=TEXT_DIM, wrap=490)

    def show(self) -> None:
        cfg = self._services.config
        dpg.set_value(self._theme, cfg["application"]["theme"])
        dpg.set_value(self._layout, cfg["application"]["default_layout"])
        dpg.set_value(self._rec_dir, cfg["recording"]["directory"])
        dpg.set_value(self._rec_mode, cfg["recording"]["mode"])
        dpg.set_value(self._queue, int(cfg["recording"]["queue_frames"]))
        dpg.set_value(self._min_free, float(cfg["recording"]["min_free_gb"]))
        dpg.set_value(self._snap_dir, cfg["snapshots"]["directory"])
        dpg.set_value(self._reconnect, bool(cfg["reconnect"]["enabled"]))
        dpg.set_value(self._log_level, cfg["logging"]["level"])
        dpg.set_value(self._message, "")
        dpg.show_item(self.window)
        dpg.focus_item(self.window)

    def _save(self) -> None:
        services = self._services
        new = copy.deepcopy(services.config)
        new["application"]["theme"] = dpg.get_value(self._theme)
        new["application"]["default_layout"] = dpg.get_value(self._layout)
        new["recording"]["directory"] = dpg.get_value(self._rec_dir).strip() or "recordings"
        new["recording"]["mode"] = dpg.get_value(self._rec_mode)
        new["recording"]["queue_frames"] = max(4, int(dpg.get_value(self._queue)))
        new["recording"]["min_free_gb"] = max(0.0, float(dpg.get_value(self._min_free)))
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
        dpg.configure_item(self._message, color=STATE_COLORS[CameraState.ERROR] if error else STATE_COLORS[CameraState.ACQUIRING])
