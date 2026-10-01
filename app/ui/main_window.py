"""Main window: menu bar, header toolbar, camera sidebar, multiview, status bar."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import dearpygui.dearpygui as dpg

from app.cameras.camera_device import CameraError
from app.models.camera_state import CameraState
from app.services.app_services import AppServices
from app.services.configuration import save_config
from app.services.diagnostics import export_diagnostics
from app.ui import dialogs
from app.ui.camera_controls import CameraControlsPanel
from app.ui.camera_sidebar import CameraSidebar
from app.ui.log_panel import LogPanel
from app.ui.multiview import MultiView
from app.ui.performance_window import PerformanceWindow
from app.ui.settings_window import SettingsWindow
from app.ui.status_panel import StatusPanel
from app.ui.theme import STATE_COLORS, TEXT_DIM, create_theme, load_fonts, square_window_theme, use_font
from app.ui.toolbar import Toolbar, format_recording_status

logger = logging.getLogger(__name__)

APP_TITLE = "LUCID Camera Studio"
SIDEBAR_WIDTH = 300
HEADER_HEIGHT = 40
STATUS_HEIGHT = 30
SPACING = 6  # matches mvStyleVar_ItemSpacing y in theme.py


class MainWindow:
    def __init__(self, services: AppServices) -> None:
        self._services = services
        self._manager = services.manager
        app_cfg = services.config["application"]
        self._ui_fps = 0.0
        self._ui_frames = 0
        self._ui_window_start = time.perf_counter()
        self._bottom_height = 0
        ui_cfg = services.config["ui"]
        load_fonts()  # before building widgets so use_font() can apply heading/mono faces

        with dpg.window(tag="main_window", menubar=True):
            with dpg.menu_bar():
                with dpg.menu(label="File"):
                    dpg.add_menu_item(label="Save session...", callback=self._save_session)
                    dpg.add_menu_item(label="Load session...", callback=self._load_session)
                    dpg.add_separator()
                    dpg.add_menu_item(label="Settings...", callback=lambda: self._settings.show())
                    dpg.add_menu_item(label="Export diagnostics", callback=self._export_diagnostics)
                    dpg.add_separator()
                    dpg.add_menu_item(label="Exit", callback=lambda: dpg.stop_dearpygui())
                with dpg.menu(label="View"):
                    dpg.add_menu_item(label="Performance", callback=lambda: self._performance.toggle())
                    with dpg.menu(label="Theme"):
                        dpg.add_menu_item(label="Dark", callback=lambda: self.set_theme("dark", persist=True))
                        dpg.add_menu_item(label="Light", callback=lambda: self.set_theme("light", persist=True))
                with dpg.menu(label="Cameras"):
                    dpg.add_menu_item(label="Start all", callback=lambda: self._sidebar.start_all())
                    dpg.add_menu_item(label="Stop all", callback=lambda: self._sidebar.stop_all())
                    dpg.add_separator()
                    self._reconnect_item = dpg.add_menu_item(
                        label="Auto-reconnect",
                        check=True,
                        default_value=services.reconnect.enabled,
                        callback=lambda _s, value: self._set_reconnect(value),
                    )

            with dpg.child_window(height=HEADER_HEIGHT, border=False, no_scrollbar=True):
                with dpg.group(horizontal=True):
                    app_title = dpg.add_text(APP_TITLE)
                    use_font(app_title, "heading")
                    dpg.add_spacer(width=24)
                    self._header_status = dpg.add_text("", color=TEXT_DIM)
                    dpg.add_spacer(width=24)
                    with dpg.group() as toolbar_parent:
                        self._toolbar = Toolbar(toolbar_parent, services.recording)

            with dpg.group(horizontal=True):
                with dpg.child_window(width=SIDEBAR_WIDTH, height=-STATUS_HEIGHT - 8) as sidebar:
                    self._sidebar_window = sidebar
                    self._sidebar = CameraSidebar(sidebar, self._manager, services.statuses)
                    self._controls = CameraControlsPanel(
                        sidebar, self._manager, services.controls, services.profiles, wrap=SIDEBAR_WIDTH - 30
                    )
                with dpg.child_window(width=-1, height=-STATUS_HEIGHT - 8, no_scrollbar=True) as area:
                    self._area_window = area
                    self._multiview = MultiView(
                        area, self._manager, app_cfg["default_layout"], services.recording, services.reconnect
                    )

            # Collapsible sections below the multiview; the stream area takes whatever they free up.
            self._status_panel = StatusPanel(
                "main_window", services.statuses, ui_cfg["stats_refresh_hz"], default_open=ui_cfg["status_panel_open"]
            )
            self._log_panel = LogPanel(
                "main_window", services.logs, ui_cfg["stats_refresh_hz"], default_open=ui_cfg["log_panel_open"],
                theme=app_cfg["theme"],
            )

            with dpg.child_window(height=STATUS_HEIGHT, border=False, no_scrollbar=True):
                self._status = dpg.add_text("", color=TEXT_DIM)

        self._performance = PerformanceWindow(services.performance, self._multiview.display_fps, lambda: self._ui_fps)
        self._settings = SettingsWindow(services, on_theme=self.set_theme)
        self.set_theme(app_cfg["theme"])
        dpg.bind_item_theme("main_window", square_window_theme())
        dpg.set_primary_window("main_window", True)

    # --- per frame ------------------------------------------------------------
    def update(self) -> None:
        """Called once per rendered frame from the UI thread."""
        self._toolbar.update()
        self._sidebar.update()
        self._controls.update(self._sidebar.selected)
        self._multiview.update()
        self._status_panel.update()
        self._log_panel.update()
        self._fit_main_area()
        self._performance.update()
        self._update_ui_fps()

        camera_ids = self._manager.camera_ids
        states = [self._manager.state(cid) for cid in camera_ids]
        streaming = states.count(CameraState.ACQUIRING)
        errors = states.count(CameraState.ERROR)
        total_fps = sum(s.measured_fps for cid in camera_ids if (s := self._manager.stats(cid)) is not None)

        if errors:
            header_state = CameraState.ERROR
        elif streaming:
            header_state = CameraState.ACQUIRING
        else:
            header_state = CameraState.DISCONNECTED
        dpg.set_value(self._header_status, f"● {streaming}/{len(camera_ids)} streaming")
        dpg.configure_item(self._header_status, color=STATE_COLORS[header_state])

        error_text = f" | {errors} error(s)" if errors else ""
        dpg.set_value(
            self._status,
            f"{len(camera_ids)} Cameras | {streaming} streaming | {total_fps:.1f} FPS"
            f"{error_text} | Layout {self._multiview.layout} | {format_recording_status(self._toolbar.status)}",
        )

    def _fit_main_area(self) -> None:
        """Give the sidebar/multiview row all height not used by the sections below it."""
        sections = dpg.get_item_rect_size(self._status_panel.group)[1] + dpg.get_item_rect_size(self._log_panel.group)[1]
        bottom = int(sections + STATUS_HEIGHT + 3 * SPACING)
        if bottom != self._bottom_height:
            self._bottom_height = bottom
            for window in (self._sidebar_window, self._area_window):
                dpg.configure_item(window, height=-bottom)

    def _update_ui_fps(self) -> None:
        self._ui_frames += 1
        now = time.perf_counter()
        if now - self._ui_window_start >= 1.0:
            self._ui_fps = self._ui_frames / (now - self._ui_window_start)
            self._ui_frames, self._ui_window_start = 0, now

    # --- actions --------------------------------------------------------------
    def set_theme(self, name: str, persist: bool = False) -> None:
        dpg.bind_theme(create_theme(name))
        if hasattr(self, "_log_panel"):
            self._log_panel.set_theme(name)
        if persist:
            self._services.config["application"]["theme"] = name
            self._persist_config()

    def _set_reconnect(self, enabled: bool) -> None:
        self._services.reconnect.enabled = enabled
        self._services.config["reconnect"]["enabled"] = enabled
        self._persist_config()

    def _persist_config(self) -> None:
        try:
            save_config(self._services.config, self._services.config_path)
        except OSError as exc:
            logger.error("Could not save settings: %s", exc)

    def _save_session(self) -> None:
        def save(name: str) -> None:
            if not name.strip():
                return
            try:
                path = self._services.sessions.save(name.strip(), self._multiview.layout)
            except (CameraError, OSError) as exc:
                dialogs.message("Save session", f"Could not save session: {exc}")
                return
            dialogs.message("Save session", f"Session saved to {path}")

        dialogs.prompt_text("Save session", "Session name", save)

    def _load_session(self) -> None:
        def load(name: str) -> None:
            try:
                result = self._services.sessions.load(name)
            except CameraError as exc:
                dialogs.message("Load session", str(exc))
                return
            if result.layout:
                self._multiview.set_layout(result.layout)
            lines = [f"Loaded '{name}'. Started: {', '.join(result.started) or 'none'}."]
            if result.warnings:
                lines += ["", "Warnings:", *(f"- {w}" for w in result.warnings)]
            dialogs.message("Load session", "\n".join(lines))

        dialogs.choose(
            "Load session", "Session", self._services.sessions.list_sessions(), load,
            empty_text="No saved sessions yet. Use File > Save session first.",
        )

    def _export_diagnostics(self) -> None:
        services = self._services
        try:
            path = export_diagnostics(
                Path(services.config["diagnostics"]["directory"]), services.manager, services.config, services.log_dir
            )
        except OSError as exc:
            dialogs.message("Export diagnostics", f"Export failed: {exc}")
            return
        dialogs.message("Export diagnostics", f"Diagnostics saved to {path.resolve()}")
