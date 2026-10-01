"""Settings panel for the selected camera: exposure, gain, frame rate, pixel format, ROI.

Controls are built from the camera's reported capabilities: unsupported features are disabled and
labelled, ranges come from the camera. All changes go through CameraControlService.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import dearpygui.dearpygui as dpg

from app.cameras.camera_device import CameraError, NumericRange, Roi
from app.cameras.camera_manager import CameraManager
from app.models.camera_state import CameraState
from app.services.camera_control_service import CameraControlService, ControlSnapshot
from app.services.profile_service import ProfileService
from app.ui.theme import STATE_COLORS, TEXT_DIM

logger = logging.getLogger(__name__)

REFRESH_INTERVAL_S = 2.0
ERROR_COLOR = STATE_COLORS[CameraState.ERROR]
OK_COLOR = STATE_COLORS[CameraState.ACQUIRING]


@dataclass
class _NumericRow:
    input: int | str
    range_text: int | str


class CameraControlsPanel:
    def __init__(
        self,
        parent: int | str,
        manager: CameraManager,
        service: CameraControlService,
        profiles: ProfileService,
        wrap: int,
    ) -> None:
        self._manager = manager
        self._service = service
        self._profiles = profiles
        self._camera_id: str | None = None
        self._last_state: CameraState | None = None
        self._last_refresh = 0.0

        dpg.add_spacer(height=10, parent=parent)
        dpg.add_separator(parent=parent)
        self._title = dpg.add_text("CAMERA SETTINGS", color=TEXT_DIM, parent=parent)
        self._hint = dpg.add_text("Select a camera to edit its settings", color=TEXT_DIM, wrap=wrap, parent=parent)
        self._message = dpg.add_text("", wrap=wrap, parent=parent, show=False)

        with dpg.group(parent=parent, show=False) as self._controls:
            self._exposure = self._numeric_row("Exposure (µs)", "%.0f", self._on_exposure)
            self._gain = self._numeric_row("Gain (dB)", "%.2f", self._on_gain, slider=True)
            self._frame_rate = self._numeric_row("Frame rate (Hz)", "%.2f", self._on_frame_rate)

            dpg.add_spacer(height=4)
            dpg.add_text("Pixel format")
            self._pixel_format = dpg.add_combo([], width=-1, callback=self._on_pixel_format)

            dpg.add_spacer(height=4)
            dpg.add_text("Region of interest")
            with dpg.group(horizontal=True):
                self._roi_x = dpg.add_input_int(label="X", width=95, step=0)
                self._roi_y = dpg.add_input_int(label="Y", width=95, step=0)
            with dpg.group(horizontal=True):
                self._roi_w = dpg.add_input_int(label="W", width=95, step=0)
                self._roi_h = dpg.add_input_int(label="H", width=95, step=0)
            with dpg.group(horizontal=True):
                self._roi_apply = dpg.add_button(label="Apply ROI", callback=self._on_roi_apply)
                self._roi_full = dpg.add_button(label="Full frame", callback=self._on_roi_full)
            self._roi_text = dpg.add_text("", color=TEXT_DIM, wrap=wrap)

            dpg.add_spacer(height=4)
            dpg.add_text("Profile")
            with dpg.group(horizontal=True):
                self._profile_combo = dpg.add_combo([], width=180, no_preview=False)
                dpg.add_button(label="Apply", callback=self._on_profile_apply)
            with dpg.group(horizontal=True):
                self._profile_name = dpg.add_input_text(hint="New profile name", width=180)
                dpg.add_button(label="Save", callback=self._on_profile_save)

        self._inputs = [
            self._exposure.input, self._gain.input, self._frame_rate.input, self._pixel_format,
            self._roi_x, self._roi_y, self._roi_w, self._roi_h, self._profile_combo, self._profile_name,
        ]

    # --- per-frame --------------------------------------------------------
    def update(self, selected_id: str | None) -> None:
        if selected_id != self._camera_id:
            self._camera_id = selected_id
            self._show_message("")
            self.refresh()
            return
        if selected_id is None:
            return
        state = self._manager.state(selected_id)
        due = time.monotonic() - self._last_refresh > REFRESH_INTERVAL_S
        # Don't overwrite a field the user is editing.
        if (state != self._last_state or due) and not any(dpg.is_item_active(i) for i in self._inputs):
            self.refresh()

    def refresh(self) -> None:
        self._last_refresh = time.monotonic()
        camera_id = self._camera_id
        if camera_id is None:
            dpg.set_value(self._title, "CAMERA SETTINGS")
            dpg.set_value(self._hint, "Select a camera to edit its settings")
            dpg.show_item(self._hint)
            dpg.hide_item(self._controls)
            return

        self._last_state = self._manager.state(camera_id)
        dpg.set_value(self._title, f"CAMERA SETTINGS — {camera_id}")
        try:
            snap = self._service.snapshot(camera_id)
        except CameraError as exc:
            self._show_message(str(exc), error=True)
            return

        if not snap.connected:
            dpg.set_value(self._hint, "Start the camera to connect and edit its settings")
            dpg.show_item(self._hint)
            dpg.hide_item(self._controls)
            return

        if snap.streaming:
            dpg.set_value(self._hint, "Pixel format and ROI changes briefly restart the stream")
            dpg.show_item(self._hint)
        else:
            dpg.hide_item(self._hint)
        dpg.show_item(self._controls)
        self._populate(snap)

    # --- building / populating --------------------------------------------
    def _numeric_row(self, label: str, fmt: str, callback: Callable, slider: bool = False) -> _NumericRow:
        dpg.add_spacer(height=4)
        with dpg.group(horizontal=True):
            dpg.add_text(label)
            range_text = dpg.add_text("", color=TEXT_DIM)
        if slider:
            widget = dpg.add_slider_float(width=-1, format=fmt, callback=callback)
        else:
            widget = dpg.add_input_float(width=-1, format=fmt, step=0, on_enter=True, callback=callback)
        return _NumericRow(widget, range_text)

    def _populate(self, snap: ControlSnapshot) -> None:
        self._refresh_profiles()
        self._set_numeric(self._exposure, snap.exposure, snap.exposure_range)
        self._set_numeric(self._gain, snap.gain, snap.gain_range)
        self._set_numeric(self._frame_rate, snap.frame_rate, snap.frame_rate_range)

        dpg.configure_item(self._pixel_format, items=snap.pixel_formats, enabled=bool(snap.pixel_formats))
        dpg.set_value(self._pixel_format, snap.pixel_format if snap.pixel_formats else "not supported")

        roi_supported = snap.roi_limits is not None
        for item in (self._roi_x, self._roi_y, self._roi_w, self._roi_h, self._roi_apply, self._roi_full):
            dpg.configure_item(item, enabled=roi_supported)
        if roi_supported:
            roi, limits = snap.roi, snap.roi_limits
            for item, value in ((self._roi_x, roi.x), (self._roi_y, roi.y), (self._roi_w, roi.width), (self._roi_h, roi.height)):
                dpg.set_value(item, value)
            dpg.set_value(
                self._roi_text,
                f"Sensor {limits.sensor_width}×{limits.sensor_height}, "
                f"step W{limits.width_increment} H{limits.height_increment} "
                f"X{limits.offset_x_increment} Y{limits.offset_y_increment}",
            )
        else:
            dpg.set_value(self._roi_text, "ROI not supported by this camera")

    def _refresh_profiles(self) -> None:
        names = self._profiles.list_profiles()
        dpg.configure_item(self._profile_combo, items=names)
        if dpg.get_value(self._profile_combo) not in names:
            dpg.set_value(self._profile_combo, names[0] if names else "")

    def _set_numeric(self, row: _NumericRow, value: float | None, value_range: NumericRange | None) -> None:
        supported = value_range is not None and value is not None
        dpg.configure_item(row.input, enabled=supported)
        if not supported:
            dpg.set_value(row.range_text, "not supported")
            return
        dpg.set_value(row.range_text, f"{_fmt(value_range.minimum)} – {_fmt(value_range.maximum)}")
        if dpg.get_item_type(row.input).endswith("mvSliderFloat"):
            dpg.configure_item(row.input, min_value=value_range.minimum, max_value=value_range.maximum)
        dpg.set_value(row.input, value)

    # --- callbacks ----------------------------------------------------------
    def _on_exposure(self, _sender, value) -> None:
        self._apply(lambda cid: self._service.set_exposure(cid, value), "Exposure set to {:g} µs")

    def _on_gain(self, _sender, value) -> None:
        self._apply(lambda cid: self._service.set_gain(cid, value), "Gain set to {:g} dB")

    def _on_frame_rate(self, _sender, value) -> None:
        self._apply(lambda cid: self._service.set_frame_rate(cid, value), "Frame rate set to {:g} Hz")

    def _on_pixel_format(self, _sender, value) -> None:
        self._apply(lambda cid: self._service.set_pixel_format(cid, value), f"Pixel format set to {value}")

    def _on_roi_apply(self) -> None:
        roi = Roi(*(int(dpg.get_value(i)) for i in (self._roi_x, self._roi_y, self._roi_w, self._roi_h)))
        self._apply(lambda cid: self._service.set_roi(cid, roi), "ROI set to {0.width}×{0.height} at ({0.x}, {0.y})")

    def _on_roi_full(self) -> None:
        self._apply(lambda cid: self._service.reset_roi(cid), "ROI reset to full frame {0.width}×{0.height}")

    def _on_profile_apply(self) -> None:
        name = dpg.get_value(self._profile_combo)
        if not name or self._camera_id is None:
            self._show_message("Choose a profile to apply", error=True)
            return
        try:
            warnings = self._profiles.apply(self._camera_id, name)
        except CameraError as exc:
            self._show_message(str(exc), error=True)
        else:
            if warnings:
                self._show_message(f"Profile '{name}' applied with warnings: " + "; ".join(warnings), error=True)
            else:
                self._show_message(f"Profile '{name}' applied")
        self.refresh()

    def _on_profile_save(self) -> None:
        name = dpg.get_value(self._profile_name).strip()
        if not name or self._camera_id is None:
            self._show_message("Type a name for the new profile", error=True)
            return
        try:
            path = self._profiles.save(self._camera_id, name)
        except (CameraError, OSError, ValueError) as exc:
            self._show_message(str(exc), error=True)
            return
        dpg.set_value(self._profile_name, "")
        self._refresh_profiles()
        dpg.set_value(self._profile_combo, name)
        self._show_message(f"Profile '{name}' saved to {path}")

    def _apply(self, action: Callable[[str], object], success: str) -> None:
        camera_id = self._camera_id
        if camera_id is None:
            return
        try:
            result = action(camera_id)
        except CameraError as exc:
            logger.warning("Setting change on %s failed: %s", camera_id, exc)
            self._show_message(str(exc), error=True)
        else:
            self._show_message(success.format(result))
        self.refresh()

    def _show_message(self, text: str, error: bool = False) -> None:
        dpg.set_value(self._message, text)
        dpg.configure_item(self._message, color=ERROR_COLOR if error else OK_COLOR, show=bool(text))


def _fmt(value: float) -> str:
    """Readable range bound: 1,000,000 rather than 1e+06; keeps decimals for small values."""
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    return f"{value:.4g}"
