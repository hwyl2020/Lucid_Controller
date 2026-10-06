"""One camera in the sidebar list: a card with a header line plus its own expandable control panel.

Header:  [disclosure] [state dot] Model (Serial), then IP · state
Panel (this camera only): power switch + stream button, video recording (format + record),
image capture (format + capture), live statistics and the Property Grid button.

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
from app.services.network_service import NetworkService
from app.services.recording_service import RecordingError, RecordingService
from app.ui import dialogs
from app.ui import theme
from app.ui.theme import COLORS, STATE_COLORS, STATE_NAMES, THEME_TEXT, bind, caption, secondary_text, use_font
from app.ui.status_bar import format_duration
from app.ui.widgets import Switch
from app.camera_log import camera_logger

logger = logging.getLogger(__name__)

SUBTITLE_INDENT = 34  # aligns the IP / state line under the camera name
CONTROL_WIDTH = 150
STREAM_WIDTH = 104
PLAY, STOP = "►", "■"  # Segoe UI has U+25BA/U+25A0 (not U+25B6)
STREAM_LABELS = {False: f"{PLAY}  Start", True: f"{STOP}  Stop"}
PENDING_LABELS = {"check": "Checking…", "close": "Closing…", "force": "Forcing IP…",
                  "start": "Starting…", "stop": "Stopping…"}
PANEL_TEXT_WRAP = 300
DETAILS_REFRESH_S = 0.2
DASH = "—"
VIDEO_FORMATS = {mode.label: mode for mode in RecordingMode}
IMAGE_FORMAT_LABELS = {label: key for key, (_ext, label) in IMAGE_FORMATS.items()}


def _borderless_table(**kwargs) -> int | str:
    return dpg.table(header_row=False, borders_innerH=False, borders_outerH=False, borders_innerV=False,
                     borders_outerV=False, **kwargs)


class CameraRow:
    def __init__(
        self,
        parent: int | str,
        status: CameraStatus,
        manager: CameraManager,
        statuses: CameraStatusService,
        recording: RecordingService,
        network: NetworkService,
        on_select: Callable[[str], None],
        on_property_grid: Callable[[str], None],
    ) -> None:
        self.camera_id = status.camera_id
        self._log = camera_logger(logger, status.camera_id)
        self._manager = manager
        self._statuses = statuses
        self._recording = recording
        self._network = network
        self._notice: tuple[str, bool] | None = None  # (text, is_error) from the last power action
        self._choose_adapter = None  # (check, candidates) waiting for the adapter dialog
        self._on_select = on_select
        self._on_property_grid = on_property_grid
        self.expanded = False
        self.selected = False
        self._pending: str | None = None  # "open" / "close" / "start" / "stop" while a worker is busy
        self._last_details = 0.0
        self._bound: dict[int | str, str | None] = {}  # item -> bound role (rebind only on change)
        self._dot_key: tuple | None = None

        with dpg.child_window(parent=parent, auto_resize_y=True, no_scrollbar=True) as self.card:
            with dpg.group() as title_stack:  # no gap between the name line and the IP line
                with dpg.group(horizontal=True, horizontal_spacing=6) as self.header:
                    self.arrow = dpg.add_button(arrow=True, direction=dpg.mvDir_Right, callback=self.toggle_expanded)
                    self.dot = dpg.add_text("●")
                    self.name = dpg.add_selectable(label=status.display_name, width=0,
                                                   callback=lambda: self._on_select(self.camera_id))
                with dpg.group(horizontal=True, horizontal_spacing=6):
                    dpg.add_spacer(width=SUBTITLE_INDENT)
                    self.ip = secondary_text(status.ip_address or "No IP")
                    sep = secondary_text("·")
                    self.state_text = secondary_text("")
            bind(title_stack, "stack")
            use_font(self.name, "heading")
            for item in (self.ip, sep, self.state_text):
                use_font(item, "small")
            use_font(self.dot, "caption")
            bind(self.arrow, "ghost")
            bind(self.name, "quiet_selectable")

            with dpg.group(show=False) as self.panel:
                dpg.add_separator()
                # Power switch + stream button on one line.
                with _borderless_table(policy=dpg.mvTable_SizingFixedFit) as power_row:
                    dpg.add_table_column(width_stretch=True)
                    dpg.add_table_column(width_fixed=True, init_width_or_weight=STREAM_WIDTH)
                    with dpg.table_row():
                        with dpg.group(horizontal=True, horizontal_spacing=10):
                            self._switch = Switch(None, self.toggle_power)
                            self.power_button = self._switch.button
                            self.power_label = dpg.add_text("Off")
                        self.stream_button = dpg.add_button(label=STREAM_LABELS[False], width=STREAM_WIDTH,
                                                            callback=self.toggle_stream)
                bind(power_row, "tight")
                use_font(self.power_label, "heading")
                self.acq_text = secondary_text("", wrap=PANEL_TEXT_WRAP, show=False)
                use_font(self.acq_text, "small")

                caption("Video recording")
                with dpg.group(horizontal=True):
                    self.video_format = dpg.add_combo(list(VIDEO_FORMATS), default_value=recording.default_mode.label,
                                                      width=CONTROL_WIDTH)
                    self.rec_button = dpg.add_button(label="●  Record", width=-1, callback=self.toggle_recording)
                self.rec_text = secondary_text("", wrap=PANEL_TEXT_WRAP, show=False)
                use_font(self.rec_text, "small")

                caption("Image capture")
                with dpg.group(horizontal=True):
                    self.image_format = dpg.add_combo(list(IMAGE_FORMAT_LABELS), default_value="PNG",
                                                      width=CONTROL_WIDTH)
                    self.capture_button = dpg.add_button(label="Capture", width=-1, callback=self.capture)
                self.capture_text = secondary_text("", wrap=PANEL_TEXT_WRAP, show=False)
                use_font(self.capture_text, "small")

                # Live statistics as three metric blocks (value over caption).
                with _borderless_table(policy=dpg.mvTable_SizingStretchSame) as metrics:
                    for _ in range(3):
                        dpg.add_table_column()
                    with dpg.table_row():
                        self._metrics = []
                        for label in ("Mb/s", "FPS", "Frames"):
                            with dpg.group() as block:
                                self._metrics.append(dpg.add_text(DASH))
                                caption(label, upper=False)
                            bind(block, "stack")
                bind(metrics, "compact_table")
                for item in self._metrics:
                    use_font(item, "metric")
                self.stats_text = self._metrics[0]
                self.grid_button = dpg.add_button(label="Property Grid…", width=-1,
                                                  callback=lambda: self._on_property_grid(self.camera_id))
        bind(self.card, "card")

    def delete(self) -> None:
        """Remove the card (the camera was unplugged)."""
        if dpg.does_item_exist(self.card):
            dpg.delete_item(self.card)

    # --- expand / select ----------------------------------------------------------
    def toggle_expanded(self) -> None:
        self.set_expanded(not self.expanded)

    def set_expanded(self, expanded: bool) -> None:
        self.expanded = expanded
        dpg.configure_item(self.arrow, direction=dpg.mvDir_Down if expanded else dpg.mvDir_Right)
        dpg.configure_item(self.panel, show=expanded)
        self._last_details = 0.0

    def set_selected(self, selected: bool) -> None:
        self.selected = selected
        dpg.set_value(self.name, selected)
        bind(self.card, "card_selected" if selected else "card")

    # --- power / streaming -------------------------------------------------------------
    @property
    def camera_on(self) -> bool:
        return self._manager.camera(self.camera_id).connected

    @property
    def busy(self) -> bool:
        return self._pending is not None

    def toggle_power(self) -> None:
        """ON: if the camera is not on a host adapter's subnet, the first click forces its IP and the
        next click opens it; otherwise it opens directly (no streaming).
        OFF: finishes recording, stops streaming, closes the camera."""
        if not self._pending:
            self._run("close" if self.camera_on else "check")

    def toggle_stream(self) -> None:
        if self._pending or not self.camera_on:
            return
        acquiring = self._manager.state(self.camera_id) is CameraState.ACQUIRING
        self._run("stop" if acquiring else "start")

    def _run(self, action: str) -> None:
        self._pending = action
        threading.Thread(target=self._do, args=(action,), daemon=True, name=f"{action}-{self.camera_id}").start()

    def _do(self, action: str, *args) -> None:
        cid = self.camera_id
        try:
            if action == "check":
                check = self._network.check(cid)
                if check is None or check.reachable:
                    self._open()
                else:
                    self._fix_ip(check)
            elif action == "force":
                self._force(*args)
            elif action == "start":
                self._manager.start_streaming(cid)
            else:  # "stop" or "close": finish the recording file before the stream goes away
                if self._recording.is_recording(cid):
                    self._recording.stop([cid])
                if action == "stop":
                    self._manager.stop_streaming(cid)
                else:
                    self._manager.disconnect(cid)
        except (CameraError, ValueError) as exc:
            self._log.error("Could not %s %s: %s", action, cid, exc)
            self._notice = (str(exc), True)
        except KeyError:
            self._log.info("%s: camera was removed (unplugged) during %s", cid, action)
        finally:
            self._pending = None

    def _open(self) -> None:
        self._manager.stop_streaming(self.camera_id)  # clears a failed stream, if any
        self._manager.connect(self.camera_id)
        self._notice = None

    def _fix_ip(self, check) -> None:
        """First ON click for an unreachable camera: force its IP (camera stays off)."""
        candidates = self._network.candidate_interfaces(check)
        if not candidates:
            self._notice = (f"Camera is at {check.camera_ip}, but this PC has no network adapter with an "
                            "IPv4 address to move it to.", True)
        elif len(candidates) == 1:
            self._force(check, candidates[0])
        else:
            self._choose_adapter = (check, candidates)  # the UI thread shows the dialog

    def _force(self, check, interface) -> None:
        plan = self._network.plan(check, interface)
        self._network.force(self.camera_id, plan)
        self._notice = (f"Camera was at {check.camera_ip}, not on this PC's subnet. IP forced to {plan.ip} "
                        f"(adapter {interface}). Click ON again to turn the camera on.", False)

    def _ask_adapter(self, check, candidates) -> None:
        labels = {f"{i}  (adapter MAC {i.mac})" if i.mac else str(i): i for i in candidates}

        def chosen(label: str) -> None:
            self._pending = "force"
            threading.Thread(target=self._do, args=("force", check, labels[label]), daemon=True,
                             name=f"force-{self.camera_id}").start()

        dialogs.choose("Force IP", f"The camera is at {check.camera_ip}. Move it to the subnet of which adapter "
                       "(the one the camera is plugged into)?", list(labels), chosen,
                       empty_text="No suitable network adapter found.")

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
    def _bind(self, item: int | str, role_name: str | None) -> None:
        """Bind a role theme only when it changes (binding every frame is wasted work)."""
        if self._bound.get(item, "") != role_name:
            self._bound[item] = role_name
            dpg.bind_item_theme(item, theme.role(role_name) if role_name else 0)

    def update(self) -> None:
        state = self._manager.state(self.camera_id)
        acquiring = state is CameraState.ACQUIRING
        camera_on = self.camera_on
        recording = self._recording.is_recording(self.camera_id)
        dot_key = (state, recording, theme.revision())
        if dot_key != self._dot_key:
            self._dot_key = dot_key
            dpg.configure_item(self.dot, color=STATE_COLORS[state])
            if recording:
                dpg.set_value(self.state_text, "Recording")
                dpg.configure_item(self.state_text, color=COLORS["error"])
            else:
                dpg.set_value(self.state_text, STATE_NAMES[state])
                emphasised = state in (CameraState.ACQUIRING, CameraState.ERROR)
                dpg.configure_item(self.state_text, color=STATE_COLORS[state] if emphasised else THEME_TEXT)
        pending = self._pending
        if self._choose_adapter is not None:  # dialogs must be created on the UI thread
            check, candidates = self._choose_adapter
            self._choose_adapter = None
            self._ask_adapter(check, candidates)
        if pending in ("check", "close", "force"):
            self._switch.set(pending == "close", False, PENDING_LABELS[pending])
            dpg.set_value(self.power_label, PENDING_LABELS[pending])
        else:
            self._switch.set(camera_on, pending is None, "ON" if camera_on else "OFF")
            dpg.set_value(self.power_label, "On" if camera_on else "Off")
        if pending in ("start", "stop"):
            dpg.configure_item(self.stream_button, label=PENDING_LABELS[pending], enabled=False)
            self._bind(self.stream_button, None)
        else:
            dpg.configure_item(self.stream_button, label=STREAM_LABELS[acquiring],
                               enabled=camera_on and pending is None)
            self._bind(self.stream_button, None if acquiring or not camera_on else "primary")
        dpg.configure_item(self.rec_button, label="■  Stop recording" if recording else "●  Record",
                           enabled=acquiring or recording)
        self._bind(self.rec_button, "danger" if recording else ("record_idle" if acquiring else None))
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
        if self._notice is not None and not camera_on:
            text, is_error = self._notice
            dpg.set_value(self.acq_text, text)
            dpg.configure_item(self.acq_text, show=True, color=COLORS["error"] if is_error else COLORS["warning"])
        elif status.error:
            self._set_text(self.acq_text, status.error, error=True)
        elif not camera_on:
            self._set_text(self.acq_text, "Camera is off. Switch it on to stream.")
        else:
            self._set_text(self.acq_text, "Streaming" if acquiring else
                           "On, not streaming · stream-locked settings can be changed")
        current = self._recording.camera_recording(self.camera_id)
        if current is not None:
            st = current.stats
            dropped = st.frame_gaps + st.queue_overflows
            self._set_text(self.rec_text, f"● REC {format_duration(current.elapsed_s)} · {st.frames_written:,} frames"
                           f" · {dropped} dropped", error=bool(dropped or st.error))
        values = ((f"{status.bandwidth_mbps:,.1f}", f"{status.fps:.2f}", f"{status.frame_count:,}")
                  if acquiring else (DASH, DASH, DASH))
        for item, value in zip(self._metrics, values):
            dpg.set_value(item, value)

    @staticmethod
    def _set_text(item, text: str, error: bool = False) -> None:
        """Secondary-coloured status text (the item is bound to the secondary text role), or red."""
        dpg.set_value(item, text)
        dpg.configure_item(item, color=COLORS["error"] if error else THEME_TEXT, show=bool(text))
