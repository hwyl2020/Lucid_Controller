"""Per-camera rows: each camera's controls act on that camera only (no viewport needed)."""

import copy
import time

import dearpygui.dearpygui as dpg
import pytest

from app.cameras.camera_manager import CameraManager
from app.cameras.simulator_camera import SimulatorCamera, SimulatorConfig
from app.models.camera_state import CameraState
from app.services.configuration import DEFAULT_CONFIG
from app.services.recording_service import RecordingService
from app.services.camera_status_service import CameraStatusService
from app.ui.camera_sidebar import CameraSidebar


def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def sidebar(tmp_path):
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["recording"]["directory"] = str(tmp_path / "rec")
    cfg["recording"]["min_free_gb"] = 0
    cfg["snapshots"]["directory"] = str(tmp_path / "snap")
    manager = CameraManager(frame_timeout=0.1)
    for cid, ip in (("A", "192.168.1.101"), ("B", "192.168.1.102"), ("C", None)):
        manager.add_camera(SimulatorCamera(SimulatorConfig(camera_id=cid, serial_number=f"SN-{cid}", ip_address=ip,
                                                           width=320, height=240, fps=50)))
    recording = RecordingService(manager, cfg)
    opened = []
    dpg.create_context()
    with dpg.window():
        with dpg.group() as parent:
            pass
    bar = CameraSidebar(parent, manager, CameraStatusService(manager), recording, on_property_grid=opened.append)
    yield bar, manager, recording, opened, tmp_path
    recording.stop()
    manager.shutdown()
    dpg.destroy_context()


def test_rows_show_model_serial_and_dynamic_ip(sidebar):
    bar, *_ = sidebar
    assert [dpg.get_item_label(r.name) for r in bar.rows.values()] == [
        "Simulator (SN-A)", "Simulator (SN-B)", "Simulator (SN-C)"]
    assert [dpg.get_value(r.ip) for r in bar.rows.values()] == ["192.168.1.101", "192.168.1.102", "No IP"]


def test_expanding_one_camera_only(sidebar):
    bar, *_ = sidebar
    bar.set_expanded("B", True)
    assert {cid: r.expanded for cid, r in bar.rows.items()} == {"A": False, "B": True, "C": False}


def test_independent_acquisition_toggles(sidebar):
    bar, manager, *_ = sidebar
    bar.rows["A"].toggle_acquisition()
    bar.rows["C"].toggle_acquisition()
    assert wait_until(lambda: not bar.busy)
    states = {cid: manager.state(cid) for cid in bar.rows}
    assert states == {"A": CameraState.ACQUIRING, "B": CameraState.DISCONNECTED, "C": CameraState.ACQUIRING}
    for row in bar.rows.values():
        row.update()
    assert [dpg.get_item_label(r.acq_button) for r in bar.rows.values()] == ["ON", "OFF", "ON"]
    bar.rows["A"].toggle_acquisition()
    assert wait_until(lambda: not bar.busy)
    assert manager.state("A") is CameraState.CONNECTED and manager.state("C") is CameraState.ACQUIRING


def test_independent_recording_and_capture(sidebar):
    bar, manager, recording, _, tmp_path = sidebar
    for cid in ("A", "B"):
        bar.rows[cid].toggle_acquisition()
    assert wait_until(lambda: not bar.busy and manager.snapshot_frame("B") is not None)
    dpg.set_value(bar.rows["A"].video_format, "MKV")
    bar.rows["A"].toggle_recording()
    assert recording.is_recording("A") and not recording.is_recording("B")
    assert recording.camera_recording("A").mode.label == "MKV"

    dpg.set_value(bar.rows["B"].image_format, "TIFF")
    bar.rows["B"].capture()
    files = sorted(p.name for p in (tmp_path / "snap").rglob("*.*"))
    assert files and all(name.startswith("B_") for name in files)
    assert any(name.endswith(".tif") for name in files)

    bar.rows["C"].toggle_recording()  # C is not acquiring
    assert not recording.is_recording("C") and "Start the camera" in dpg.get_value(bar.rows["C"].rec_text)
    bar.rows["A"].toggle_recording()
    assert not recording.is_recording("A")


def test_stopping_acquisition_finishes_that_cameras_recording(sidebar):
    bar, manager, recording, *_ = sidebar
    for cid in ("A", "B"):
        bar.rows[cid].toggle_acquisition()
    assert wait_until(lambda: not bar.busy)
    recording.start(camera_ids=["A"])
    recording.start(camera_ids=["B"])
    bar.rows["A"].toggle_acquisition()
    assert wait_until(lambda: not bar.busy)
    assert not recording.is_recording("A") and recording.is_recording("B")


def test_property_grid_button_targets_its_camera(sidebar):
    bar, _, _, opened, _ = sidebar
    bar.rows["B"]._on_property_grid(bar.rows["B"].camera_id)
    assert opened == ["B"]
