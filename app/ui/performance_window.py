"""Performance window: host metric tiles plus a per-camera acquisition/recording table."""

from __future__ import annotations

from collections.abc import Callable

import dearpygui.dearpygui as dpg

from app.models.units import format_mbps
from app.services.performance_monitor import PerformanceMonitor
from app.ui.theme import COLORS, STATE_COLORS, STATE_NAMES, THEME_TEXT, bind, caption, secondary_text, use_font

COLUMNS = ("Camera", "State", "Cam FPS", "Disp FPS", "Mb/s", "Missed", "Timeouts", "NIC", "Rec queue", "Rec dropped")
TILES = ("CPU", "Memory", "Disk write", "UI")
TILE_WIDTH = 214
TILE_HEIGHT = 72


class PerformanceWindow:
    def __init__(self, monitor: PerformanceMonitor, display_fps: Callable[[str], float], ui_fps: Callable[[], float]) -> None:
        self._monitor = monitor
        self._display_fps = display_fps
        self._ui_fps = ui_fps
        self._rows: dict[str, list[int | str]] = {}
        self._tiles: dict[str, tuple[int | str, int | str]] = {}  # name -> (value, detail)
        with dpg.window(label="Performance", width=940, height=420, show=False, pos=(200, 120), no_collapse=True) as self.window:
            with dpg.group(horizontal=True, horizontal_spacing=10):
                for name in TILES:
                    with dpg.child_window(width=TILE_WIDTH, height=TILE_HEIGHT, no_scrollbar=True) as tile:
                        caption(name)
                        with dpg.group(horizontal=True, horizontal_spacing=8):
                            value = dpg.add_text("—")
                            detail = secondary_text("")
                    bind(tile, "stat_tile")
                    use_font(value, "metric")
                    use_font(detail, "small")
                    self._tiles[name] = (value, detail)
            self._nics = secondary_text("")
            use_font(self._nics, "small")
            with dpg.table(header_row=True, borders_innerH=False, borders_outerH=False, row_background=True,
                           resizable=True, policy=dpg.mvTable_SizingStretchProp) as self._table:
                for column in COLUMNS:
                    dpg.add_table_column(label=column)
            bind(self._table, "compact_table")
            note = secondary_text(
                "Missed = frames the camera sent that never arrived (frame-id gaps). "
                "Rec dropped = frames not recorded (queue overflow or missed).",
                wrap=880,
            )
            use_font(note, "small")

    @property
    def visible(self) -> bool:
        return dpg.is_item_shown(self.window)

    def toggle(self) -> None:
        dpg.configure_item(self.window, show=not self.visible)

    def _tile(self, name: str, value: str, detail: str) -> None:
        value_item, detail_item = self._tiles[name]
        dpg.set_value(value_item, value)
        dpg.set_value(detail_item, detail)

    def update(self) -> None:
        if not self.visible:
            return
        sample = self._monitor.sample()
        host = sample.host
        self._tile("CPU", f"{host.cpu_percent:.0f}%", f"app {host.process_cpu_percent:.0f}%")
        self._tile("Memory", f"{host.memory_percent:.0f}%", f"app {host.process_memory_mb:,.0f} MB")
        self._tile("Disk write", format_mbps(host.disk_write_mbps), f"rec {format_mbps(host.recording_write_mbps)}")
        self._tile("UI", f"{self._ui_fps():.0f}", "FPS")
        busy = {nic: rate for nic, rate in host.nic_rx_mbps.items() if rate >= 0.5}
        dpg.set_value(
            self._nics, "Network receive:  " + ("   ·   ".join(f"{n} {format_mbps(r)}" for n, r in sorted(busy.items()))
                                                or "idle")
        )

        present = {cam.status.camera_id for cam in sample.cameras}
        for camera_id in [cid for cid in self._rows if cid not in present]:  # unplugged cameras
            cells = self._rows.pop(camera_id)
            if cells and dpg.does_item_exist(cells[0]):
                dpg.delete_item(dpg.get_item_parent(cells[0]))
        for cam in sample.cameras:
            st = cam.status
            cells = self._rows.get(st.camera_id)
            if cells is None:
                with dpg.table_row(parent=self._table):
                    cells = [dpg.add_text("") for _ in COLUMNS]
                self._rows[st.camera_id] = cells
            streaming = st.acquiring
            values = (
                st.display_name,
                STATE_NAMES[st.state] + ("  ● REC" if cam.recording else ""),
                f"{st.fps:.1f}" if streaming else "—",
                f"{self._display_fps(st.camera_id):.1f}" if streaming else "—",
                f"{st.bandwidth_mbps:,.1f}" if streaming else "—",
                str(st.frames_missed),
                str(st.timeouts),
                cam.nic or "—",
                str(cam.recording_queue_depth) if cam.recording else "—",
                str(cam.recording_dropped) if cam.recording else "—",
            )
            for cell, value in zip(cells, values):
                dpg.set_value(cell, value)
            dpg.configure_item(cells[1], color=STATE_COLORS[st.state])
            dpg.configure_item(cells[5], color=COLORS["error"] if st.frames_missed else THEME_TEXT)
            dpg.configure_item(cells[9], color=COLORS["error"] if cam.recording_dropped else THEME_TEXT)
