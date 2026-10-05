"""Main window: menu bar, header, camera sidebar, multiview, status/log sections, status bar.

Recording and snapshots are per camera (camera rows); the header has no global record controls."""

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
from app.ui.camera_sidebar import CameraSidebar
from app.ui.log_panel import LogPanel
from app.ui.multiview import MultiView
from app.ui.performance_window import PerformanceWindow
from app.ui.property_grid import PropertyGridWindow
from app.ui.settings_window import SettingsWindow
from app.ui.status_panel import StatusPanel
from app.ui import theme
from app.ui.theme import ACCENTS, COLORS, THEME_TEXT, bind, load_fonts, secondary_text, use_font
from app.ui.status_bar import format_recording_status

logger = logging.getLogger(__name__)

APP_TITLE = "LUCID Camera Studio"
SIDEBAR_WIDTH = 392  # fits Model (Serial) + IP on one line
HEADER_HEIGHT = 46
STATUS_HEIGHT = 30
SPACING = 8  # matches mvStyleVar_ItemSpacing y in theme.py
THEME_LABELS = {"dark": "Dark", "light": "Light"}


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
                    dpg.add_separator()
                    with dpg.menu(label="Theme"):
                        self._theme_items = {
                            name: dpg.add_menu_item(label=label, check=True,
                                                    callback=lambda _s, _a, n: self.set_theme(n, persist=True),
                                                    user_data=name)
                            for name, label in THEME_LABELS.items()
                        }
                    with dpg.menu(label="Accent colour"):
                        self._accent_items = {
                            name: dpg.add_menu_item(label=name, check=True,
                                                    callback=lambda _s, _a, n: self.set_accent(n, persist=True),
                                                    user_data=name)
                            for name in ACCENTS
                        }
                with dpg.menu(label="Cameras"):
                    self._reconnect_item = dpg.add_menu_item(
                        label="Auto-reconnect",
                        check=True,
                        default_value=services.reconnect.enabled,
                        callback=lambda _s, value: self._set_reconnect(value),
                    )

            # Toolbar: title and live summary pills on the left, layout picker on the right.
            with dpg.child_window(height=HEADER_HEIGHT, no_scrollbar=True, no_scroll_with_mouse=True) as header:
                with dpg.table(header_row=False, policy=dpg.mvTable_SizingFixedFit, borders_innerH=False,
                               borders_outerH=False, borders_innerV=False, borders_outerV=False) as header_table:
                    dpg.add_table_column(width_stretch=True)
                    dpg.add_table_column(width_fixed=True)
                    with dpg.table_row():
                        with dpg.group(horizontal=True, horizontal_spacing=16):
                            app_title = dpg.add_text(APP_TITLE)
                            with theme.nudge(3):
                                with dpg.group(horizontal=True, horizontal_spacing=8):
                                    self._live_pill = dpg.add_button(label="", height=24)
                                    self._rec_pill = dpg.add_button(label="", height=24, show=False)
                                    self._error_pill = dpg.add_button(label="", height=24, show=False)
                        with dpg.group(horizontal=True, horizontal_spacing=10):
                            with theme.nudge(1):
                                layout_caption = secondary_text("Layout")
                            self._layout_slot = dpg.add_group()
            bind(header, "canvas")
            bind(header_table, "tight")
            use_font(app_title, "title")
            use_font(layout_caption, "small")
            for pill in (self._live_pill, self._rec_pill, self._error_pill):
                use_font(pill, "caption")
            self._pill_roles: dict[int | str, str] = {}

            with dpg.group(horizontal=True, horizontal_spacing=12):
                with dpg.child_window(width=SIDEBAR_WIDTH, height=-STATUS_HEIGHT - 8, border=True) as sidebar:
                    self._sidebar_window = sidebar
                    self._sidebar = CameraSidebar(
                        sidebar, self._manager, services.statuses, services.recording, services.profiles,
                        services.network, self.open_property_grid,
                    )
                with dpg.child_window(width=-1, height=-STATUS_HEIGHT - 8, no_scrollbar=True) as area:
                    self._area_window = area
                    self._multiview = MultiView(
                        area, self._manager, app_cfg["default_layout"], services.recording, services.reconnect
                    )
            bind(sidebar, "surface")
            bind(area, "canvas")
            self._multiview.build_layout_control(self._layout_slot)

            # Collapsible sections below the multiview; the stream area takes whatever they free up.
            self._status_panel = StatusPanel(
                "main_window", services.statuses, ui_cfg["stats_refresh_hz"], default_open=ui_cfg["status_panel_open"]
            )
            self._log_panel = LogPanel(
                "main_window", services.logs, ui_cfg["stats_refresh_hz"], default_open=ui_cfg["log_panel_open"],
                theme=app_cfg["theme"],
                camera_names=lambda: {st.camera_id: st.display_name for st in services.statuses.statuses()},
            )

            with dpg.child_window(height=STATUS_HEIGHT, border=True, no_scrollbar=True,
                                  no_scroll_with_mouse=True) as status_bar:
                with dpg.table(header_row=False, policy=dpg.mvTable_SizingFixedFit, borders_innerH=False,
                               borders_outerH=False, borders_innerV=False, borders_outerV=False) as status_table:
                    dpg.add_table_column(width_stretch=True)
                    dpg.add_table_column(width_fixed=True)
                    with dpg.table_row():
                        self._status = secondary_text("")
                        self._status_rec = secondary_text("")
            bind(status_bar, "bar")
            bind(status_table, "tight")
            use_font(self._status, "small")
            use_font(self._status_rec, "small")
            self._status_rec_active: bool | None = None

        self._property_grids: dict[str, PropertyGridWindow] = {}
        self._performance = PerformanceWindow(services.performance, self._multiview.display_fps, lambda: self._ui_fps)
        self._settings = SettingsWindow(services, on_theme=self.set_theme, on_accent=self.set_accent)
        self._theme_name = app_cfg["theme"]
        self.set_accent(app_cfg.get("accent", theme.DEFAULT_ACCENT))
        bind("main_window", "square_window")
        dpg.set_primary_window("main_window", True)

    # --- per frame ------------------------------------------------------------
    def update(self) -> None:
        """Called once per rendered frame from the UI thread."""
        # Polled every frame: also enforces the low-disk auto-stop of active recordings.
        recording_status = self._services.recording.status()
        self._sidebar.update()
        self._multiview.update()
        self._status_panel.update()
        self._log_panel.update()
        self._fit_main_area()
        self._performance.update()
        for grid in list(self._property_grids.values()):
            grid.update()
        self._update_ui_fps()

        camera_ids = self._manager.camera_ids
        states = [self._manager.state(cid) for cid in camera_ids]
        streaming = states.count(CameraState.ACQUIRING)
        errors = states.count(CameraState.ERROR)
        total_fps = sum(s.measured_fps for cid in camera_ids if (s := self._manager.stats(cid)) is not None)

        recording = len(recording_status.cameras) if recording_status.active else 0
        self._set_pill(self._live_pill, f"\u25cf  {streaming} of {len(camera_ids)} live",
                       "pill_success" if streaming else "pill_neutral")
        self._set_pill(self._rec_pill, f"\u25cf  REC {recording}", "pill_rec", show=bool(recording))
        self._set_pill(self._error_pill, f"{errors} error{'s' if errors != 1 else ''}", "pill_error", show=bool(errors))

        sep = "   \u00b7   "
        dpg.set_value(
            self._status,
            f"{len(camera_ids)} camera{'s' if len(camera_ids) != 1 else ''}{sep}{streaming} live{sep}"
            f"{total_fps:.1f} FPS total{sep}Layout {self._multiview.layout.replace('x', ' \u00d7 ')}"
            f"{sep}UI {self._ui_fps:.0f} FPS",
        )
        dpg.set_value(self._status_rec, format_recording_status(recording_status).replace(" | ", sep))
        active = recording_status.active or bool(recording_status.error)
        if active != self._status_rec_active:
            self._status_rec_active = active
            dpg.configure_item(self._status_rec, color=COLORS["error"] if active else THEME_TEXT)

    def _set_pill(self, pill, label: str, role_name: str, show: bool = True) -> None:
        if dpg.get_item_label(pill) != label:
            dpg.configure_item(pill, label=label)
        if dpg.is_item_shown(pill) != show:
            dpg.configure_item(pill, show=show)
        if self._pill_roles.get(pill) != role_name:
            self._pill_roles[pill] = role_name
            bind(pill, role_name)

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
        self._theme_name = name
        theme.apply(name, theme.current_accent())
        self._after_theme_change()
        if persist:
            self._services.config["application"]["theme"] = name
            self._persist_config()

    def set_accent(self, accent: str, persist: bool = False) -> None:
        theme.apply(self._theme_name, accent)
        self._after_theme_change()
        if persist:
            self._services.config["application"]["accent"] = accent
            self._persist_config()

    def _after_theme_change(self) -> None:
        for name, item in self._theme_items.items():
            dpg.set_value(item, name == theme.current_theme())
        for name, item in self._accent_items.items():
            dpg.set_value(item, name == theme.current_accent())
        self._log_panel.set_theme(theme.current_theme())
        self._status_rec_active = None  # recolour on the next update

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

    def open_property_grid(self, camera_id: str) -> None:
        """One floating Property Grid per camera; reopening focuses the existing window."""
        grid = self._property_grids.get(camera_id)
        if grid is not None:
            grid.focus()
            return
        camera = self._manager.camera(camera_id)
        offset = 30 * len(self._property_grids)
        self._property_grids[camera_id] = PropertyGridWindow(
            camera_id,
            f"{camera.model} ({camera.serial_number})",
            self._services.features,
            on_close=lambda cid: self._property_grids.pop(cid, None),
            state_of=self._manager.state,
            pos=(SIDEBAR_WIDTH + 48 + offset, 100 + offset),
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
