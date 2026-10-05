"""Log records carry the camera they concern; the log panel shows and filters by camera."""

import logging
import time

import dearpygui.dearpygui as dpg
import pytest

from app.camera_log import CameraFieldFilter, camera_logger, for_camera, record_camera
from app.cameras.camera_manager import CameraManager
from app.cameras.simulator_camera import SimulatorCamera, SimulatorConfig
from app.services.log_buffer import LogBuffer
from app.ui.log_panel import LogPanel, matches_camera


@pytest.fixture
def buffer():
    buf = LogBuffer()
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(buf)
    root.setLevel(logging.INFO)
    yield buf
    root.removeHandler(buf)
    root.setLevel(old_level)


def test_helpers_tag_records(buffer):
    log = logging.getLogger("test.camera_log")
    log.info("app-wide")
    log.info("one call", extra=for_camera("CAM-1"))
    camera_logger(log, "CAM-2").warning("adapter %s", "x", extra={"other": 1})
    assert [(e.camera_id, e.message) for e in buffer.entries()[-3:]] == [
        (None, "app-wide"), ("CAM-1", "one call"), ("CAM-2", "adapter x")]


def test_file_format_field_defaults_for_untagged_records():
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", None, None)
    assert CameraFieldFilter().filter(record) and record.camera_id == "-" and record_camera(record) is None


def test_camera_operations_are_tagged(buffer):
    manager = CameraManager(frame_timeout=0.1)
    for cid in ("A", "B"):
        manager.add_camera(SimulatorCamera(SimulatorConfig(camera_id=cid, width=160, height=120, fps=50)))
    try:
        manager.connect("A")
        manager.connect("B")
        manager.start_streaming("B")
        time.sleep(0.2)
        manager.stop_streaming("B")
    finally:
        manager.shutdown()
    by_camera = {}
    for entry in buffer.entries():
        by_camera.setdefault(entry.camera_id, []).append(entry.message)
    assert any("Connected A" in m for m in by_camera["A"])
    assert any("Acquisition started for B" in m for m in by_camera["B"])
    assert not any("B" in m.split() for m in by_camera.get("A", []))  # B's records are not tagged A


def test_matches_camera():
    from app.services.log_buffer import LogEntry

    tagged = LogEntry(1, 0.0, logging.INFO, "INFO", "x", "m", "A")
    system = LogEntry(2, 0.0, logging.INFO, "INFO", "x", "m", None)
    assert matches_camera(tagged, None) and matches_camera(system, None)
    assert matches_camera(tagged, "A") and not matches_camera(tagged, "B") and not matches_camera(system, "A")
    assert matches_camera(system, "") and not matches_camera(tagged, "")


def test_log_panel_shows_camera_column_and_filters(buffer):
    log = logging.getLogger("test.panel")
    names = {"263401242": "TRI122S-C (263401242)", "SIM-01": "Simulator (SIM00000001)"}
    dpg.create_context()
    try:
        with dpg.window():
            with dpg.group() as parent:
                pass
        panel = LogPanel(parent, buffer, refresh_hz=50, default_open=True, camera_names=lambda: names)
        buffer.clear()
        log.info("startup")
        log.info("frame from tri", extra=for_camera("263401242"))
        log.info("frame from sim", extra=for_camera("SIM-01"))

        def rows():
            panel._dirty = True
            panel.update()
            return [[dpg.get_value(c) for c in dpg.get_item_children(r, 1)][2:] for r in panel._rows]

        assert rows() == [["System", "startup"], ["TRI122S-C (263401242)", "frame from tri"],
                          ["Simulator (SIM00000001)", "frame from sim"]]
        panel.set_camera_filter("263401242")
        assert rows() == [["TRI122S-C (263401242)", "frame from tri"]]
        panel.set_camera_filter("")
        assert rows() == [["System", "startup"]]
        panel._on_camera_filter("Simulator (SIM00000001)")  # via the dropdown label
        assert panel.camera_filter == "SIM-01" and rows() == [["Simulator (SIM00000001)", "frame from sim"]]
        assert "TRI122S-C (263401242)" in dpg.get_item_configuration(panel._camera_combo)["items"]
    finally:
        dpg.destroy_context()
