"""One camera in the sidebar list: header line plus its own expandable control panel.

Header:  [disclosure] [state dot] Model (Serial)  IP  [⋮]
Panel (this camera only): acquisition toggle, video recording (format + record), image capture
(format + capture), Property Grid button and live statistics.

Every control acts on this row's camera only. The acquisition toggle always shows the camera's
actual state (read every frame from CameraManager); start/stop runs on a worker thread so one
camera connecting never blocks another camera's controls.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

import dearpygui.dearpygui as dpg

from app.cameras.camera_device import CameraError
from app.cameras.camera_manager import CameraManager
from app.models.camera_state import CameraState
from app.models.camera_status import CameraStatus
from app.recording.recorder import RecordingMode
from app.recording.snapshot import IMAGE_FORMATS
from app.services.camera_status_service import CameraStatusService
from app.services.recording_service import RecordingError, RecordingService
from app.ui.theme import STATE_COLORS, TEXT_DIM, compact_table_theme, plain_button_theme, use_font

logger = logging.getLogger(__name__)

NAME_WIDTH = 190
CONTROL_WIDTH = 118
PANEL_TEXT_WRAP = 300
DETAILS_REFRESH_S = 0.2
ERROR_COLOR = STATE_COLORS[CameraState.ERROR]
VIDEO_FORMATS = {mode.label: mode for mode in RecordingMode}
IMAGE_FORMAT_LABELS = {label: key for key, (_ext, label) in IMAGE_FORMATS.items()}


def _toggle_themes() -> tuple[int, int, int]:
    """(on, off, recording) button themes."""
    green, red = STATE_COLORS[CameraState.ACQUIRING], STATE_COLORS[CameraState.ERROR]
    themes = []
    for fill, text in (((*green, 60), green), ((128, 128, 128, 40), TEXT_DIM), ((*red, 70), (255, 255, 255))):
        with dpg.theme() as theme:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, fill)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, fill)
                dpg.add_theme_color(dpg.mvThemeCol_Text, text)
        themes.append(theme)
    return tuple(themes)


class CameraRow:
    _themes: tuple[int, int, int] | None = None
    _plain: int | None = None
    _compact: int | None = None

    def __init__(
        self,
        parent: int | str,
        status: CameraStatus,
        manager: CameraManager,
        statuses: CameraStatusService,
        recording: RecordingService,
        on_select: Callable[[str], None],
        on_property_grid: Callable[[str], None],
    ) -> None:
        self.camera_id = status.camera_id
        self._manager = manager
        self._statuses = statuses
        self._recording = recording
        self._on_select = on_select
        self._on_property_grid = on_property_grid
        self.expanded = False
        self._pending: str | None = None  # "start" / "stop" while a worker thread is busy
        self._last_details = 0.0
        if CameraRow._themes is None or not dpg.does_item_exist(CameraRow._themes[0]):
            CameraRow._themes = _toggle_themes()
            CameraRow._plain = plain_button_theme()
            CameraRow._compact = compact_table_theme()

        with dpg.group(horizontal=True, horizontal_spacing=4, parent=parent) as self.header:
            self.arrow = dpg.add_button(arrow=True, direction=dpg.mvDir_Right, callback=self.toggle_expanded)
            self.dot = dpg.add_text("●")
            self.name = dpg.add_selectable(label=status.display_name, width=NAME_WIDTH,
                                           callback=lambda: self._on_select(self.camera_id))
            self.ip = dpg.add_text(status.ip_address or "No IP", color=TEXT_DIM)
            self.menu_button = dpg.add_button(label="···", width=24)  # U+22EE is missing from Segoe UI
        for button in (self.arrow, self.menu_button):
            dpg.bind_item_theme(button, CameraRow._plain)
        with dpg.tooltip(self.name):
            dpg.add_text(f"{status.display_name}\nCamera ID: {self.camera_id}")
        with dpg.popup(self.menu_button, mousebutton=dpg.mvMouseButton_Left):
            dpg.add_menu_item(label="Property Grid…", callback=lambda: self._on_property_grid(self.camera_id))
            dpg.add_menu_item(label="Start / stop acquisition", callback=self.toggle_acquisition)
            dpg.add_menu_item(label="Capture image", callback=self.capture)
            dpg.add_menu_item(label="Show controls", callback=lambda: self.set_expanded(True))

        with dpg.child_window(parent=parent, auto_resize_y=True, border=True, show=False) as self.panel:
            with dpg.table(header_row=False, policy=dpg.mvTable_SizingFixedFit, borders_innerH=False,
                           borders_outerH=False, borders_innerV=False, borders_outerV=False) as table:
                dpg.add_table_column(width_stretch=True)
                dpg.add_table_column(width_fixed=True, init_width_or_weight=CONTROL_WIDTH)
                with dpg.table_row():
                    title = dpg.add_text("Acquisition")
                    self.acq_button = dpg.add_button(label="OFF", width=CONTROL_WIDTH, callback=self.toggle_acquisition)
                use_font(title, "heading")
            dpg.bind_item_theme(table, CameraRow._compact)
            self.acq_text = dpg.add_text("", color=TEXT_DIM, wrap=PANEL_TEXT_WRAP)
            dpg.add_separator()

            rec_title = dpg.add_text("Video Recording")
            use_font(rec_title, "heading")
            with dpg.group(horizontal=True):
                self.video_format = dpg.add_combo(list(VIDEO_FORMATS), default_value=recording.default_mode.label,
                                                  width=CONTROL_WIDTH)
                self.rec_button = dpg.add_button(label="● Record", width=CONTROL_WIDTH,
                                                 callback=self.toggle_recording)
            self.rec_text = dpg.add_text("", color=TEXT_DIM, wrap=PANEL_TEXT_WRAP)
            dpg.add_separator()

            cap_title = dpg.add_text("Image Capture")
            use_font(cap_title, "heading")
            with dpg.group(horizontal=True):
                self.image_format = dpg.add_combo(list(IMAGE_FORMAT_LABELS), default_value="PNG", width=CONTROL_WIDTH)
                self.capture_button = dpg.add_button(label="Capture", width=CONTROL_WIDTH, callback=self.capture)
            self.capture_text = dpg.add_text("", color=TEXT_DIM, wrap=PANEL_TEXT_WRAP)
            dpg.add_separator()

            self.grid_button = dpg.add_button(label="Property Grid", width=-1,
                                              callback=lambda: self._on_property_grid(self.camera_id))
            self.stats_text = dpg.add_text("", color=TEXT_DIM)

    # --- expand / select ----------------------------------------------------------
    def toggle_expanded(self) -> None:
        self.set_expanded(not self.expanded)

    def set_expanded(self, expanded: bool) -> None:
        self.expanded = expanded
        dpg.configure_item(self.arrow, direction=dpg.mvDir_Down if expanded else dpg.mvDir_Right)
        dpg.configure_item(self.panel, show=expanded)
        self._last_details = 0.0

    def set_selected(self, selected: bool) -> None:
        dpg.set_value(self.name, selected)

    # --- acquisition ----------------------------------------------------------------
    def toggle_acquisition(self) -> None:
        if self._pending:
            return
        acquiring = self._manager.state(self.camera_id) is CameraState.ACQUIRING
        self._pending = "stop" if acquiring else "start"
        threading.Thread(target=self._run_acquisition, args=(not acquiring,), daemon=True,
                         name=f"acq-toggle-{self.camera_id}").start()

    def start(self) -> None:
        if self._manager.state(self.camera_id) is not CameraState.ACQUIRING and not self._pending:
            self.toggle_acquisition()

    def stop(self) -> None:
        if self._manager.state(self.camera_id) is CameraState.ACQUIRING and not self._pending:
            self.toggle_acquisition()

    @property
    def busy(self) -> bool:
        return self._pending is not None

    def _run_acquisition(self, start: bool) -> None:
        try:
            if start:
                if self._manager.state(self.camera_id) in (CameraState.DISCONNECTED, CameraState.ERROR):
                    self._manager.stop_streaming(self.camera_id)
                    self._manager.connect(self.camera_id)
                self._manager.start_streaming(self.camera_id)
            else:
                if self._recording.is_recording(self.camera_id):
                    self._recording.stop([self.camera_id])  # finish the file before the stream stops
                self._manager.stop_streaming(self.camera_id)
        except CameraError as exc:
            logger.error("Could not %s %s: %s", "start" if start else "stop", self.camera_id, exc)
        finally:
            self._pending = None

    # --- recording / capture -----------------------------------------------------------
    def toggle_recording(self) -> None:
        try:
            if self._recording.is_recording(self.camera_id):
                status = self._recording.stop([self.camera_id])
                stats = status.cameras.get(self.camera_id)
                dropped = (stats.frame_gaps + stats.queue_overflows) if stats else 0
                self._set_text(self.rec_text, f"Saved {stats.frames_written if stats else 0} frames"
                               f"{f', {dropped} dropped' if dropped else ''} to {status.session_dir.name}",
                               error=bool(dropped))
            else:
                mode = VIDEO_FORMATS[dpg.get_value(self.video_format)]
                self._recording.start(mode, [self.camera_id])
        except (RecordingError, CameraError, OSError) as exc:
            self._set_text(self.rec_text, str(exc), error=True)

    def capture(self) -> None:
        image_format = IMAGE_FORMAT_LABELS[dpg.get_value(self.image_format)]
        try:
            files = self._recording.snapshot([self.camera_id], image_format)
        except (RecordingError, CameraError, OSError) as exc:
            self._set_text(self.capture_text, str(exc), error=True)
            return
        saved = files[0].processed or files[0].raw
        self._set_text(self.capture_text, f"Saved {saved.name}")

    # --- per frame -------------------------------------------------------------------
    def update(self) -> None:
        state = self._manager.state(self.camera_id)
        acquiring = state is CameraState.ACQUIRING
        dpg.configure_item(self.dot, color=STATE_COLORS[state])
        on, off, rec = CameraRow._themes
        if self._pending:
            dpg.configure_item(self.acq_button, label="Starting…" if self._pending == "start" else "Stopping…",
                               enabled=False)
        else:
            dpg.configure_item(self.acq_button, label="ON" if acquiring else "OFF", enabled=True)
            dpg.bind_item_theme(self.acq_button, on if acquiring else off)
        recording = self._recording.is_recording(self.camera_id)
        dpg.configure_item(self.rec_button, label="■ Stop" if recording else "● Record", enabled=acquiring or recording)
        dpg.bind_item_theme(self.rec_button, rec if recording else 0)
        dpg.configure_item(self.video_format, enabled=not recording)
        dpg.configure_item(self.capture_button, enabled=acquiring)

        now = time.monotonic()
        if now - self._last_details < DETAILS_REFRESH_S:
            return
        self._last_details = now
        status = self._statuses.status(self.camera_id)
        dpg.set_value(self.ip, status.ip_address or "No IP")
        if not self.expanded:
            return
        connection = "Connected" if status.connected else "Disconnected"
        activity = "acquiring" if acquiring else "stopped"
        if status.error:
            self._set_text(self.acq_text, status.error, error=True)
        else:
            self._set_text(self.acq_text, f"{connection} · {activity}")
        current = self._recording.camera_recording(self.camera_id)
        if current is not None:
            minutes, seconds = divmod(int(current.elapsed_s), 60)
            st = current.stats
            dropped = st.frame_gaps + st.queue_overflows
            self._set_text(self.rec_text, f"● REC {minutes:02d}:{seconds:02d} · {st.frames_written:,} frames"
                           f" · {dropped} dropped", error=bool(dropped or st.error))
        dpg.set_value(self.stats_text, f"{status.bandwidth_mbps:,.1f} Mb/s · {status.fps:.2f} FPS · "
                      f"{status.frame_count:,} frames" if acquiring else "Not acquiring")

    @staticmethod
    def _set_text(item, text: str, error: bool = False) -> None:
        dpg.set_value(item, text)
        dpg.configure_item(item, color=ERROR_COLOR if error else TEXT_DIM)
