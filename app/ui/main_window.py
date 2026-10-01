"""Main window: header, camera sidebar, multiview, status bar."""

from __future__ import annotations

import dearpygui.dearpygui as dpg

from app.cameras.camera_manager import CameraManager
from app.models.camera_state import CameraState
from app.services.camera_control_service import CameraControlService
from app.ui.camera_controls import CameraControlsPanel
from app.ui.camera_sidebar import CameraSidebar
from app.ui.multiview import MultiView
from app.ui.theme import STATE_COLORS, TEXT_DIM, create_theme, load_font

APP_TITLE = "LUCID Camera Studio"
SIDEBAR_WIDTH = 300
HEADER_HEIGHT = 40
STATUS_HEIGHT = 32


class MainWindow:
    def __init__(self, config: dict, manager: CameraManager) -> None:
        self._manager = manager
        app_cfg = config["application"]

        with dpg.window(tag="main_window"):
            with dpg.child_window(height=HEADER_HEIGHT, border=False, no_scrollbar=True):
                with dpg.group(horizontal=True):
                    dpg.add_text(APP_TITLE)
                    dpg.add_spacer(width=24)
                    self._header_status = dpg.add_text("", color=TEXT_DIM)

            with dpg.group(horizontal=True):
                with dpg.child_window(width=SIDEBAR_WIDTH, height=-STATUS_HEIGHT - 8) as sidebar:
                    self._sidebar = CameraSidebar(sidebar, manager)
                    self._controls = CameraControlsPanel(
                        sidebar, manager, CameraControlService(manager), wrap=SIDEBAR_WIDTH - 30
                    )
                with dpg.child_window(width=-1, height=-STATUS_HEIGHT - 8, no_scrollbar=True) as area:
                    self._multiview = MultiView(area, manager, app_cfg["default_layout"])

            with dpg.child_window(height=STATUS_HEIGHT, border=False, no_scrollbar=True):
                self._status = dpg.add_text("")

        dpg.bind_theme(create_theme(app_cfg["theme"]))
        load_font()
        dpg.set_primary_window("main_window", True)

    def update(self) -> None:
        """Called once per rendered frame from the UI thread."""
        self._sidebar.update()
        self._controls.update(self._sidebar.selected)
        self._multiview.update()

        camera_ids = self._manager.camera_ids
        states = [self._manager.state(cid) for cid in camera_ids]
        streaming = states.count(CameraState.ACQUIRING)
        errors = states.count(CameraState.ERROR)
        total_fps = sum(
            s.measured_fps for cid in camera_ids if (s := self._manager.stats(cid)) is not None
        )

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
            f"{error_text} | Layout {self._multiview.layout} | Recording OFF",
        )
