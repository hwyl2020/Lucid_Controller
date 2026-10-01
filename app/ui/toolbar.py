"""Header toolbar: record start/stop with mode, snapshot, and a message line."""

from __future__ import annotations

import logging

import dearpygui.dearpygui as dpg

from app.cameras.camera_device import CameraError
from app.models.camera_state import CameraState
from app.recording.recorder import RecordingMode
from app.services.recording_service import RecordingError, RecordingService, RecordingStatus
from app.ui.theme import STATE_COLORS, TEXT_DIM

logger = logging.getLogger(__name__)

MODE_LABELS = {
    RecordingMode.RAW: "Raw (lossless)",
    RecordingMode.VIDEO: "Video (MP4, half res)",
}
RECORD_RED = STATE_COLORS[CameraState.ERROR]


class Toolbar:
    def __init__(self, parent: int | str, recording: RecordingService) -> None:
        self._recording = recording
        self.status = RecordingStatus(active=False)
        with dpg.group(horizontal=True, parent=parent):
            self._record_btn = dpg.add_button(label="● Record", width=110, callback=self._on_record)
            self._mode = dpg.add_combo(
                list(MODE_LABELS.values()),
                default_value=MODE_LABELS[recording.default_mode],
                width=170,
            )
            self._snapshot_btn = dpg.add_button(label="Snapshot", width=90, callback=self._on_snapshot)
            dpg.add_spacer(width=8)
            self._message = dpg.add_text("", color=TEXT_DIM)

        with dpg.theme() as self._recording_theme:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (150, 30, 30))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (190, 45, 45))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, RECORD_RED)

    def update(self) -> None:
        was_active = self.status.active
        self.status = self._recording.status()
        if self.status.active != was_active:
            dpg.configure_item(self._record_btn, label="■ Stop" if self.status.active else "● Record")
            dpg.bind_item_theme(self._record_btn, self._recording_theme if self.status.active else 0)
            dpg.configure_item(self._mode, enabled=not self.status.active)
        if not self.status.active and was_active and self.status.error:
            self._show(self.status.error, error=True)  # e.g. auto-stopped for low disk space

    def _on_record(self) -> None:
        try:
            if self._recording.active:
                status = self._recording.stop()
                self._show(
                    f"Saved {status.frames_written} frames ({status.bytes_written / 1e9:.2f} GB, "
                    f"{status.dropped} dropped) to {status.session_dir}",
                    error=bool(status.dropped or status.error),
                )
            else:
                mode = next(m for m, label in MODE_LABELS.items() if label == dpg.get_value(self._mode))
                directory = self._recording.start(mode)
                self._show(f"Recording to {directory}")
        except (RecordingError, CameraError, OSError) as exc:
            logger.error("Recording: %s", exc)
            self._show(str(exc), error=True)

    def _on_snapshot(self) -> None:
        try:
            files = self._recording.snapshot()
        except (RecordingError, CameraError, OSError) as exc:
            logger.error("Snapshot: %s", exc)
            self._show(str(exc), error=True)
            return
        self._show(f"Snapshot of {len(files)} camera(s) saved to {files[0].raw.parent}")

    def _show(self, text: str, error: bool = False) -> None:
        dpg.set_value(self._message, text)
        dpg.configure_item(self._message, color=RECORD_RED if error else TEXT_DIM)


def format_recording_status(status: RecordingStatus) -> str:
    free = f" | Free {status.free_bytes / 1e9:.0f} GB" if status.free_bytes is not None else ""
    if not status.active:
        return f"Recording OFF{free}"
    minutes, seconds = divmod(int(status.elapsed_s), 60)
    dropped = f" | {status.dropped} dropped" if status.dropped else ""
    return (
        f"● REC {minutes:02d}:{seconds:02d} | {status.frames_written} frames | "
        f"{status.bytes_written / 1e9:.2f} GB{dropped}{free}"
    )
