"""Performance window: per-camera acquisition/recording table and host metrics."""

from __future__ import annotations

from collections.abc import Callable

import dearpygui.dearpygui as dpg

from app.models.camera_state import CameraState
from app.services.performance_monitor import PerformanceMonitor
from app.ui.theme import STATE_COLORS, TEXT_DIM

COLUMNS = ("Camera", "State", "Cam FPS", "Disp FPS", "MB/s", "Missed", "Timeouts", "NIC", "Rec queue", "Rec dropped")
WARN = STATE_COLORS[CameraState.ERROR]
THEME_TEXT = (-255, 0, 0, 255)  # Dear PyGui sentinel: use the theme's text colour


class PerformanceWindow:
    def __init__(self, monitor: PerformanceMonitor, display_fps: Callable[[str], float], ui_fps: Callable[[], float]) -> None:
        self._monitor = monitor
        self._display_fps = display_fps
        self._ui_fps = ui_fps
        self._rows: dict[str, list[int | str]] = {}
        with dpg.window(label="Performance", width=900, height=330, show=False, pos=(200, 120)) as self.window:
            self._host = dpg.add_text("")
            self._nics = dpg.add_text("", color=TEXT_DIM)
            dpg.add_separator()
            with dpg.table(header_row=True, borders_innerH=True, borders_outerH=True, row_background=True,
                           resizable=True, policy=dpg.mvTable_SizingStretchProp) as self._table:
                for column in COLUMNS:
                    dpg.add_table_column(label=column)
            dpg.add_text(
                "Missed = frames the camera sent that never arrived (frame-id gaps). "
                "Rec dropped = frames not recorded (queue overflow or missed).",
                color=TEXT_DIM, wrap=860,
            )

    @property
    def visible(self) -> bool:
        return dpg.is_item_shown(self.window)

    def toggle(self) -> None:
        dpg.configure_item(self.window, show=not self.visible)

    def update(self) -> None:
        if not self.visible:
            return
        sample = self._monitor.sample()
        host = sample.host
        dpg.set_value(
            self._host,
            f"CPU {host.cpu_percent:.0f}% (app {host.process_cpu_percent:.0f}%)   "
            f"RAM {host.memory_percent:.0f}% (app {host.process_memory_mb:,.0f} MB)   "
            f"Disk write {host.disk_write_mb_s:.1f} MB/s (recording {host.recording_write_mb_s:.1f} MB/s)   "
            f"UI {self._ui_fps():.0f} FPS",
        )
        busy = {nic: rate for nic, rate in host.nic_rx_mb_s.items() if rate >= 0.05}
        dpg.set_value(self._nics, "Network receive: " + (", ".join(f"{n} {r:.1f} MB/s" for n, r in sorted(busy.items())) or "idle"))

        for cam in sample.cameras:
            cells = self._rows.get(cam.camera_id)
            if cells is None:
                with dpg.table_row(parent=self._table):
                    cells = [dpg.add_text("") for _ in COLUMNS]
                self._rows[cam.camera_id] = cells
            streaming = cam.state is CameraState.ACQUIRING
            values = (
                cam.camera_id,
                cam.state.value + (" ● REC" if cam.recording else ""),
                f"{cam.camera_fps:.1f}" if streaming else "-",
                f"{self._display_fps(cam.camera_id):.1f}" if streaming else "-",
                f"{cam.throughput_mb_s:.1f}" if streaming else "-",
                str(cam.frames_missed),
                str(cam.timeouts),
                cam.nic or "-",
                str(cam.recording_queue_depth) if cam.recording else "-",
                str(cam.recording_dropped) if cam.recording else "-",
            )
            for cell, value in zip(cells, values):
                dpg.set_value(cell, value)
            dpg.configure_item(cells[1], color=STATE_COLORS[cam.state])
            dpg.configure_item(cells[5], color=WARN if cam.frames_missed else THEME_TEXT)
            dpg.configure_item(cells[9], color=WARN if cam.recording_dropped else THEME_TEXT)
