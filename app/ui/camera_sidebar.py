"""Camera list: ``Model (Serial)`` rows with a status dot, expandable details (IP address, state),
and start/stop controls. Details come from CameraStatusService, never from the device directly."""

from __future__ import annotations

import logging
import time

import dearpygui.dearpygui as dpg

from app.cameras.camera_device import CameraError
from app.cameras.camera_manager import CameraManager
from app.models.camera_state import CameraState
from app.services.camera_status_service import CameraStatusService
from app.ui.theme import STATE_COLORS, TEXT_DIM, compact_table_theme, plain_button_theme, use_font

logger = logging.getLogger(__name__)

DETAILS_REFRESH_S = 0.5
LABEL_COLUMN = 92


class _Row:
    def __init__(self, arrow, dot, selectable, details, ip_text, state_text) -> None:
        self.arrow, self.dot, self.selectable = arrow, dot, selectable
        self.details, self.ip_text, self.state_text = details, ip_text, state_text
        self.expanded = False


class CameraSidebar:
    def __init__(self, parent: int | str, manager: CameraManager, statuses: CameraStatusService) -> None:
        self._manager = manager
        self._statuses = statuses
        self.selected: str | None = None
        self._rows: dict[str, _Row] = {}
        self._last_details = 0.0

        arrow_theme = plain_button_theme()
        details_theme = compact_table_theme()
        title = dpg.add_text("CAMERAS", color=TEXT_DIM, parent=parent)
        use_font(title, "heading")
        dpg.add_spacer(height=2, parent=parent)
        if not manager.camera_ids:
            dpg.add_text("No cameras discovered", color=TEXT_DIM, parent=parent)
        for status in statuses.statuses():
            camera_id = status.camera_id
            with dpg.group(horizontal=True, horizontal_spacing=4, parent=parent):
                arrow = dpg.add_button(arrow=True, direction=dpg.mvDir_Right,
                                       callback=self._on_toggle, user_data=camera_id)
                dpg.bind_item_theme(arrow, arrow_theme)
                dot = dpg.add_text("●")
                selectable = dpg.add_selectable(label=status.display_name, span_columns=True,
                                                callback=self._on_select, user_data=camera_id)
            with dpg.group(parent=parent, indent=30, show=False) as details:
                with dpg.table(header_row=False, borders_innerH=False, borders_outerH=False,
                               borders_innerV=False, borders_outerV=False,
                               policy=dpg.mvTable_SizingFixedFit) as details_table:
                    dpg.add_table_column(width_fixed=True, init_width_or_weight=LABEL_COLUMN)
                    dpg.add_table_column(width_stretch=True)
                    with dpg.table_row():
                        dpg.add_text("IP Address", color=TEXT_DIM)
                        ip_text = dpg.add_text("")
                    with dpg.table_row():
                        dpg.add_text("Status", color=TEXT_DIM)
                        state_text = dpg.add_text("")
                dpg.bind_item_theme(details_table, details_theme)
                dpg.add_spacer(height=2)
            with dpg.tooltip(selectable):
                dpg.add_text(f"{status.display_name}\nCamera ID: {camera_id}")
            self._rows[camera_id] = _Row(arrow, dot, selectable, details, ip_text, state_text)

        dpg.add_spacer(height=6, parent=parent)
        with dpg.group(horizontal=True, parent=parent):
            self._start_btn = dpg.add_button(label="Start", width=128, callback=lambda: self._start(self.selected))
            self._stop_btn = dpg.add_button(label="Stop", width=128, callback=lambda: self._stop(self.selected))
        with dpg.group(horizontal=True, parent=parent):
            dpg.add_button(label="Start all", width=128, callback=self.start_all)
            dpg.add_button(label="Stop all", width=128, callback=self.stop_all)

        # Start/Stop act on the selection; preselect so they work on first click.
        if manager.camera_ids:
            self._on_select(None, True, manager.camera_ids[0])

    def update(self) -> None:
        now = time.monotonic()
        refresh_details = now - self._last_details >= DETAILS_REFRESH_S
        if refresh_details:
            self._last_details = now
        for camera_id, row in self._rows.items():
            state = self._manager.state(camera_id)
            dpg.configure_item(row.dot, color=STATE_COLORS[state])
            if refresh_details and row.expanded:
                status = self._statuses.status(camera_id)
                dpg.set_value(row.ip_text, status.ip_address or "Not available")
                dpg.set_value(row.state_text, status.state.value.capitalize())
                dpg.configure_item(row.state_text, color=STATE_COLORS[status.state])
        has_selection = self.selected is not None
        dpg.configure_item(self._start_btn, enabled=has_selection)
        dpg.configure_item(self._stop_btn, enabled=has_selection)

    def start_all(self) -> None:
        for camera_id in self._manager.camera_ids:
            self._start(camera_id)

    def stop_all(self) -> None:
        for camera_id in self._manager.camera_ids:
            self._stop(camera_id)

    def is_expanded(self, camera_id: str) -> bool:
        return self._rows[camera_id].expanded

    def set_expanded(self, camera_id: str, expanded: bool) -> None:
        row = self._rows[camera_id]
        row.expanded = expanded
        dpg.configure_item(row.arrow, direction=dpg.mvDir_Down if expanded else dpg.mvDir_Right)
        dpg.configure_item(row.details, show=expanded)
        self._last_details = 0.0  # fill details on the next update

    def _on_toggle(self, _sender, _value, camera_id: str) -> None:
        self.set_expanded(camera_id, not self._rows[camera_id].expanded)

    def _on_select(self, _sender, _value, camera_id: str) -> None:
        self.selected = camera_id
        for cid, row in self._rows.items():
            dpg.set_value(row.selectable, cid == camera_id)

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
