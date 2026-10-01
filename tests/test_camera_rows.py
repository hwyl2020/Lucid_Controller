"""Per-camera rows: each camera's controls act on that camera only (no viewport needed).

Power (open/close the camera) and streaming are separate controls.
"""

import copy
import time

import dearpygui.dearpygui as dpg
import pytest

from app.cameras.camera_manager import CameraManager
from app.cameras.simulator_camera import SimulatorCamera, SimulatorConfig
from app.models.camera_state import CameraState
from app.services.camera_control_service import CameraControlService
from app.services.camera_status_service import CameraStatusService
from app.services.configuration import DEFAULT_CONFIG
from app.services.feature_service import FeatureService
from app.services.network_service import NetworkService
from app.services.profile_service import ProfileService, SettingsApplier
from app.services.recording_service import RecordingService
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
    profiles = ProfileService(manager, SettingsApplier(manager, CameraControlService(manager)), tmp_path / "profiles")
    opened = []
    dpg.create_context()
    with dpg.window():
        with dpg.group() as parent:
            pass
    bar = CameraSidebar(parent, manager, CameraStatusService(manager), recording, profiles, NetworkService(manager),
                        on_property_grid=opened.append)
    yield bar, manager, recording, opened, tmp_path
    recording.stop()
    manager.shutdown()
    dpg.destroy_context()


def power_on(bar, *ids):
    for cid in ids:
        bar.rows[cid].toggle_power()
    assert wait_until(lambda: not bar.busy)


def stream(bar, *ids):
    for cid in ids:
        bar.rows[cid].toggle_stream()
    assert wait_until(lambda: not bar.busy)


def test_rows_show_model_serial_and_dynamic_ip(sidebar):
    bar, *_ = sidebar
    assert [dpg.get_item_label(r.name) for r in bar.rows.values()] == [
        "Simulator (SN-A)", "Simulator (SN-B)", "Simulator (SN-C)"]
    assert [dpg.get_value(r.ip) for r in bar.rows.values()] == ["192.168.1.101", "192.168.1.102", "No IP"]


def test_expanding_one_camera_only(sidebar):
    bar, *_ = sidebar
    bar.set_expanded("B", True)
    assert {cid: r.expanded for cid, r in bar.rows.items()} == {"A": False, "B": True, "C": False}


def test_power_on_opens_camera_without_streaming(sidebar):
    bar, manager, *_ = sidebar
    power_on(bar, "A")
    assert manager.camera("A").connected and manager.state("A") is CameraState.CONNECTED
    assert manager.state("B") is CameraState.DISCONNECTED
    for row in bar.rows.values():
        row.update()
    a, b = bar.rows["A"], bar.rows["B"]
    assert dpg.get_item_label(a.power_button) == "ON" and dpg.get_item_label(b.power_button) == "OFF"
    assert dpg.get_item_configuration(a.stream_button)["enabled"]
    assert not dpg.get_item_configuration(b.stream_button)["enabled"]  # B is off: no streaming possible


def test_stream_button_needs_power_and_is_independent(sidebar):
    bar, manager, *_ = sidebar
    stream(bar, "B")  # B is off: ignored
    assert manager.state("B") is CameraState.DISCONNECTED
    power_on(bar, "A", "B", "C")
    stream(bar, "A", "C")
    states = {cid: manager.state(cid) for cid in bar.rows}
    assert states == {"A": CameraState.ACQUIRING, "B": CameraState.CONNECTED, "C": CameraState.ACQUIRING}
    stream(bar, "A")  # stop A's stream; camera stays on
    assert manager.state("A") is CameraState.CONNECTED and manager.camera("A").connected
    assert manager.state("C") is CameraState.ACQUIRING


def test_stream_locked_settings_editable_when_on_but_not_streaming(sidebar):
    bar, manager, *_ = sidebar
    features = FeatureService(manager)
    power_on(bar, "A")
    assert features.tree("A").find("PixelFormat").writable
    features.write("A", "PixelFormat", "RGB8")
    stream(bar, "A")
    assert features.tree("A").find("PixelFormat").access == "RO"  # locked while streaming
    stream(bar, "A")
    features.write("A", "Width", 160)
    assert manager.camera("A").roi.width == 160


def test_power_off_finishes_recording_and_closes(sidebar):
    bar, manager, recording, *_ = sidebar
    power_on(bar, "A", "B")
    stream(bar, "A", "B")
    recording.start(camera_ids=["A"])
    recording.start(camera_ids=["B"])
    bar.rows["A"].toggle_power()
    assert wait_until(lambda: not bar.busy)
    assert not manager.camera("A").connected and not recording.is_recording("A")
    assert recording.is_recording("B") and manager.state("B") is CameraState.ACQUIRING


def test_independent_recording_and_capture(sidebar):
    bar, manager, recording, _, tmp_path = sidebar
    power_on(bar, "A", "B")
    stream(bar, "A", "B")
    assert wait_until(lambda: manager.snapshot_frame("B") is not None)
    dpg.set_value(bar.rows["A"].video_format, "MKV")
    bar.rows["A"].toggle_recording()
    assert recording.is_recording("A") and not recording.is_recording("B")
    assert recording.camera_recording("A").mode.label == "MKV"

    dpg.set_value(bar.rows["B"].image_format, "TIFF")
    bar.rows["B"].capture()
    files = sorted(p.name for p in (tmp_path / "snap").rglob("*.*"))
    assert files and all(name.startswith("B_") for name in files)
    assert any(name.endswith(".tif") for name in files)

    bar.rows["C"].toggle_recording()  # C is not streaming
    assert not recording.is_recording("C") and "Start the camera" in dpg.get_value(bar.rows["C"].rec_text)
    bar.rows["A"].toggle_recording()
    assert not recording.is_recording("A")


def test_property_grid_button_targets_its_camera(sidebar):
    bar, _, _, opened, _ = sidebar
    bar.rows["B"]._on_property_grid(bar.rows["B"].camera_id)
    assert opened == ["B"]
