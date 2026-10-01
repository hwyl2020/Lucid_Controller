"""Camera list with state indicators and start/stop controls."""

from __future__ import annotations

import logging

import dearpygui.dearpygui as dpg

from app.cameras.camera_device import CameraError
from app.cameras.camera_manager import CameraManager
from app.models.camera_state import CameraState
from app.ui.theme import STATE_COLORS, TEXT_DIM

logger = logging.getLogger(__name__)


class CameraSidebar:
    def __init__(self, parent: int | str, manager: CameraManager) -> None:
        self._manager = manager
        self.selected: str | None = None
        self._rows: dict[str, tuple[int | str, int | str]] = {}  # camera_id -> (dot, selectable)

        dpg.add_text("CAMERAS", color=TEXT_DIM, parent=parent)
        dpg.add_separator(parent=parent)
        if not manager.camera_ids:
            dpg.add_text("No cameras discovered", color=TEXT_DIM, parent=parent)
        for camera_id in manager.camera_ids:
            camera = manager.camera(camera_id)
            with dpg.group(horizontal=True, parent=parent):
                dot = dpg.add_text("●")
                selectable = dpg.add_selectable(
                    label=f"{camera_id}  ({camera.model})",
                    callback=self._on_select,
                    user_data=camera_id,
                )
            self._rows[camera_id] = (dot, selectable)

        dpg.add_spacer(height=8, parent=parent)
        with dpg.group(horizontal=True, parent=parent):
            self._start_btn = dpg.add_button(label="Start", width=70, callback=lambda: self._start(self.selected))
            self._stop_btn = dpg.add_button(label="Stop", width=70, callback=lambda: self._stop(self.selected))
        with dpg.group(horizontal=True, parent=parent):
            dpg.add_button(label="Start all", width=70, callback=self.start_all)
            dpg.add_button(label="Stop all", width=70, callback=self.stop_all)

    def update(self) -> None:
        for camera_id, (dot, _) in self._rows.items():
            dpg.configure_item(dot, color=STATE_COLORS[self._manager.state(camera_id)])
        has_selection = self.selected is not None
        dpg.configure_item(self._start_btn, enabled=has_selection)
        dpg.configure_item(self._stop_btn, enabled=has_selection)

    def start_all(self) -> None:
        for camera_id in self._manager.camera_ids:
            self._start(camera_id)

    def stop_all(self) -> None:
        for camera_id in self._manager.camera_ids:
            self._stop(camera_id)

    def _on_select(self, sender, _value, camera_id: str) -> None:
        self.selected = camera_id
        for cid, (_, selectable) in self._rows.items():
            dpg.set_value(selectable, cid == camera_id)

    def _start(self, camera_id: str | None) -> None:
        if camera_id is None:
            return
        try:
            if self._manager.state(camera_id) in (CameraState.DISCONNECTED, CameraState.ERROR):
                self._manager.stop_streaming(camera_id)
                self._manager.connect(camera_id)
            self._manager.start_streaming(camera_id)
        except CameraError as exc:
            logger.error("Could not start %s: %s", camera_id, exc)

    def _stop(self, camera_id: str | None) -> None:
        if camera_id is None:
            return
        try:
            self._manager.stop_streaming(camera_id)
        except CameraError as exc:
            logger.error("Could not stop %s: %s", camera_id, exc)
