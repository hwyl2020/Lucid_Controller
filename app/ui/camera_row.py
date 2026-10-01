"""One camera in the sidebar list: header line plus its own expandable control panel.

Header:  [disclosure] [state dot] Model (Serial)  IP  [···]
Panel (this camera only): camera power toggle + stream button, video recording (format + record),
image capture (format + capture), Property Grid button and live statistics.

Power and streaming are separate on purpose: with the camera ON but not streaming, settings that the
camera locks during acquisition (pixel format, ROI, ...) can be changed in the Property Grid.

Every control acts on this row's camera only and always shows the camera's actual state (read every
frame from CameraManager). Opening/closing and starting/stopping run on a worker thread so one
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
from app.services.profile_service import ProfileService
from app.services.recording_service import RecordingError, RecordingService
from app.ui import dialogs
from app.ui.theme import STATE_COLORS, TEXT_DIM, compact_table_theme, plain_button_theme, use_font

logger = logging.getLogger(__name__)

NAME_WIDTH = 190
CONTROL_WIDTH = 118
POWER_WIDTH = 78
STREAM_WIDTH = 36
PLAY, STOP = "\u25ba", "\u25a0"  # Segoe UI has U+25BA/U+25A0 (not U+25B6)
PENDING_LABELS = {"open": "Opening\u2026", "close": "Closing\u2026", "start": "\u2026", "stop": "\u2026"}
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
        profiles: ProfileService,
        on_select: Callable[[str], None],
        on_property_grid: Callable[[str], None],
    ) -> None:
        self.camera_id = status.camera_id
        self._manager = manager
        self._statuses = statuses
        self._recording = recording
        self._profiles = profiles
        self._on_select = on_select
        self._on_property_grid = on_property_grid
        self.expanded = False
        self._pending: str | None = None  # "open" / "close" / "start" / "stop" while a worker is busy
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
            dpg.add_menu_item(label="Property Grid\u2026", callback=lambda: self._on_property_grid(self.camera_id))
            dpg.add_menu_item(label="Turn camera on / off", callback=self.toggle_power)
            dpg.add_menu_item(label="Start / stop streaming", callback=self.toggle_stream)
            dpg.add_menu_item(label="Capture image", callback=self.capture)
            dpg.add_separator()
            dpg.add_menu_item(label="Save settings as profile\u2026", callback=self._save_profile)
            dpg.add_menu_item(label="Apply profile\u2026", callback=self._apply_profile)
            dpg.add_separator()
            dpg.add_menu_item(label="Show controls", callback=lambda: self.set_expanded(True))

        with dpg.child_window(parent=parent, auto_resize_y=True, border=True, show=False) as self.panel:
            with dpg.table(header_row=False, policy=dpg.mvTable_SizingFixedFit, borders_innerH=False,
                           borders_outerH=False, borders_innerV=False, borders_outerV=False) as table:
                dpg.add_table_column(width_stretch=True)
                dpg.add_table_column(width_fixed=True, init_width_or_weight=POWER_WIDTH + STREAM_WIDTH + 8)
                with dpg.table_row():
                    title = dpg.add_text("Camera")
                    with dpg.group(horizontal=True, horizontal_spacing=8):
                        self.power_button = dpg.add_button(label="OFF", width=POWER_WIDTH, callback=self.toggle_power)
                        self.stream_button = dpg.add_button(label=PLAY, width=STREAM_WIDTH, callback=self.toggle_stream)
                use_font(title, "heading")
                with dpg.tooltip(self.power_button):
                    dpg.add_text("Turn the camera on (open it) or off (close it)")
                with dpg.tooltip(self.stream_button):
                    self.stream_tip = dpg.add_text("Start streaming")
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

    # --- power / streaming -------------------------------------------------------------
    @property
    def camera_on(self) -> bool:
        return self._manager.camera(self.camera_id).connected

    @property
    def busy(self) -> bool:
        return self._pending is not None

    def toggle_power(self) -> None:
        """ON opens the camera (no streaming); OFF finishes recording, stops streaming, closes it."""
        if not self._pending:
            self._run("close" if self.camera_on else "open")

    def toggle_stream(self) -> None:
        if self._pending or not self.camera_on:
            return
        acquiring = self._manager.state(self.camera_id) is CameraState.ACQUIRING
        self._run("stop" if acquiring else "start")

    def _run(self, action: str) -> None:
        self._pending = action
        threading.Thread(target=self._do, args=(action,), daemon=True, name=f"{action}-{self.camera_id}").start()

    def _do(self, action: str) -> None:
        cid = self.camera_id
        try:
            if action == "open":
                self._manager.stop_streaming(cid)  # clears a failed stream, if any
                self._manager.connect(cid)
            elif action == "start":
                self._manager.start_streaming(cid)
            else:  # "stop" or "close": finish the recording file before the stream goes away
                if self._recording.is_recording(cid):
                    self._recording.stop([cid])
                if action == "stop":
                    self._manager.stop_streaming(cid)
                else:
                    self._manager.disconnect(cid)
        except CameraError as exc:
            logger.error("Could not %s %s: %s", action, cid, exc)
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
        camera_on = self.camera_on
        dpg.configure_item(self.dot, color=STATE_COLORS[state])
        on, off, rec = CameraRow._themes
        pending = self._pending
        if pending in ("open", "close"):
            dpg.configure_item(self.power_button, label=PENDING_LABELS[pending], enabled=False)
        else:
            dpg.configure_item(self.power_button, label="ON" if camera_on else "OFF", enabled=pending is None)
            dpg.bind_item_theme(self.power_button, on if camera_on else off)
        if pending in ("start", "stop"):
            dpg.configure_item(self.stream_button, label=PENDING_LABELS[pending], enabled=False)
        else:
            dpg.configure_item(self.stream_button, label=STOP if acquiring else PLAY,
                               enabled=camera_on and pending is None)
            dpg.bind_item_theme(self.stream_button, on if acquiring else 0)
            dpg.set_value(self.stream_tip, "Stop streaming" if acquiring else
                          ("Start streaming" if camera_on else "Turn the camera on first"))
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
        if status.error:
            self._set_text(self.acq_text, status.error, error=True)
        elif not camera_on:
            self._set_text(self.acq_text, "Camera off")
        else:
            self._set_text(self.acq_text, "Camera on \u00b7 streaming" if acquiring else
                           "Camera on \u00b7 not streaming (stream-locked settings can be changed)")
        current = self._recording.camera_recording(self.camera_id)
        if current is not None:
            minutes, seconds = divmod(int(current.elapsed_s), 60)
            st = current.stats
            dropped = st.frame_gaps + st.queue_overflows
            self._set_text(self.rec_text, f"● REC {minutes:02d}:{seconds:02d} · {st.frames_written:,} frames"
                           f" · {dropped} dropped", error=bool(dropped or st.error))
        dpg.set_value(self.stats_text, f"{status.bandwidth_mbps:,.1f} Mb/s · {status.fps:.2f} FPS · "
                      f"{status.frame_count:,} frames" if acquiring else "Not streaming")

    # --- profiles (menu) ---------------------------------------------------------------
    def _save_profile(self) -> None:
        def save(name: str) -> None:
            try:
                path = self._profiles.save(self.camera_id, name)
            except (CameraError, OSError, ValueError) as exc:
                dialogs.message("Save profile", str(exc))
                return
            dialogs.message("Save profile", f"Profile \u201c{name.strip()}\u201d saved to {path}")
        dialogs.prompt_text("Save profile", "Profile name", save)

    def _apply_profile(self) -> None:
        def apply(name: str) -> None:
            try:
                warnings = self._profiles.apply(self.camera_id, name)
            except CameraError as exc:
                dialogs.message("Apply profile", str(exc))
                return
            text = f"Profile \u201c{name}\u201d applied."
            if warnings:
                text += "\n\nWarnings:\n" + "\n".join(f"- {w}" for w in warnings)
            dialogs.message("Apply profile", text)
        dialogs.choose("Apply profile", "Profile", self._profiles.list_profiles(), apply,
                       empty_text="No saved profiles yet. Use \u201cSave settings as profile\u201d first.")

    @staticmethod
    def _set_text(item, text: str, error: bool = False) -> None:
        dpg.set_value(item, text)
        dpg.configure_item(item, color=ERROR_COLOR if error else TEXT_DIM)
