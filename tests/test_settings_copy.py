"""Apply to all cameras (copy settings) and Reset to defaults: service, simulator, ArenaCamera, UI."""

import time

import dearpygui.dearpygui as dpg
import pytest

from app.cameras.arena_camera import ArenaCamera, filter_streamable
from app.cameras.camera_device import (
    CameraError,
    CameraNotConnectedError,
    InvalidStateError,
    UnsupportedFeatureError,
)
from app.cameras.camera_discovery import ArenaDeviceInfo
from app.cameras.camera_manager import CameraManager
from app.cameras.simulator_camera import SimulatorCamera, SimulatorConfig
from app.models.camera_state import CameraState
from app.services.feature_service import CopyResult, FeatureService, summarize_copy
from app.ui import dialogs
from app.ui.property_grid import PropertyGridWindow
from tests import fake_arena
from tests.fake_arena import FakeCommand, FakeNode


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


@pytest.fixture
def manager():
    manager = CameraManager(frame_timeout=0.1)
    for cid in ("A", "B", "C"):
        manager.add_camera(SimulatorCamera(SimulatorConfig(camera_id=cid, serial_number=f"SN-{cid}", width=320,
                                                           height=240, fps=30)))
    yield manager
    manager.shutdown()


def configure(camera):
    camera.set_exposure(20_000.0)
    camera.set_gain(6.0)
    camera.set_frame_rate(12.0)
    camera.set_pixel_format("RGB8")
    camera.set_roi(16, 8, 160, 120)


# --- FeatureService --------------------------------------------------------------
def test_copy_to_all_applies_to_cameras_that_are_on(manager):
    for cid in ("A", "B"):
        manager.connect(cid)
    configure(manager.camera("A"))
    results = FeatureService(manager).copy_to_all("A")
    assert results == [CopyResult("B", True, ""), CopyResult("C", False, "off")]
    b = manager.camera("B")
    assert (b.exposure, b.gain, b.frame_rate, b.pixel_format) == (20_000.0, 6.0, 12.0, "RGB8")
    assert (b.roi.x, b.roi.y, b.roi.width, b.roi.height) == (16, 8, 160, 120)


def test_copy_pauses_and_resumes_a_streaming_target(manager):
    for cid in ("A", "B"):
        manager.connect(cid)
    manager.start_streaming("B")
    configure(manager.camera("A"))  # pixel format/ROI are locked on B while it streams
    results = FeatureService(manager).copy_to_all("A", ["B"])
    assert results == [CopyResult("B", True, "")]
    assert manager.camera("B").pixel_format == "RGB8"
    assert manager.state("B") is CameraState.ACQUIRING


def test_copy_skips_recording_cameras(manager):
    for cid in ("A", "B"):
        manager.connect(cid)
    service = FeatureService(manager, is_recording=lambda cid: cid == "B")
    assert service.copy_to_all("A", ["B"]) == [CopyResult("B", False, "recording")]


def test_copy_from_a_camera_that_is_off_is_refused(manager):
    with pytest.raises(CameraNotConnectedError):
        FeatureService(manager).copy_to_all("A")


def test_one_failing_camera_does_not_stop_the_others(manager):
    for cid in ("A", "B", "C"):
        manager.connect(cid)

    def broken(settings):
        raise CameraError("B: load settings failed")

    manager.camera("B").import_settings = broken
    results = FeatureService(manager).copy_to_all("A")
    assert [(r.camera_id, r.applied) for r in results] == [("B", False), ("C", True)]
    assert "load settings failed" in results[0].message


def test_reset_restores_defaults_and_resumes_stream(manager):
    manager.connect("A")
    configure(manager.camera("A"))
    manager.start_streaming("A")
    FeatureService(manager).reset("A")
    a = manager.camera("A")
    assert (a.exposure, a.gain, a.frame_rate, a.pixel_format) == (10_000.0, 0.0, 30.0, "Mono8")
    assert (a.roi.width, a.roi.height) == (320, 240)
    assert manager.state("A") is CameraState.ACQUIRING


def test_reset_refused_when_off_or_recording(manager):
    with pytest.raises(CameraNotConnectedError):
        FeatureService(manager).reset("A")
    manager.connect("A")
    with pytest.raises(InvalidStateError):
        FeatureService(manager, is_recording=lambda cid: True).reset("A")


def test_simulator_clamps_settings_from_a_bigger_camera(manager):
    big = SimulatorCamera(SimulatorConfig(camera_id="BIG", width=640, height=480))
    big.connect()
    big.set_roi(0, 0, 640, 480)
    small = manager.camera("A")
    small.connect()
    small.import_settings(big.export_settings())
    assert (small.roi.width, small.roi.height) == (320, 240)


def test_summary_text():
    names = {"B": "Sim (SN-B)", "C": "Sim (SN-C)", "D": "Sim (SN-D)", "E": "Sim (SN-E)"}
    text, failed = summarize_copy([CopyResult("B", True, ""), CopyResult("C", False, "off"),
                                   CopyResult("D", False, "recording"), CopyResult("E", False, "boom")], names)
    assert failed
    assert text.splitlines() == ["Applied to 1 of 4 other camera(s): Sim (SN-B).", "Skipped Sim (SN-C): camera is off.",
                                 "Skipped Sim (SN-D): recording in progress.", "Failed on Sim (SN-E): boom"]
    assert summarize_copy([], names) == ("There are no other cameras to apply the settings to.", False)


# --- ArenaCamera (fake SDK) --------------------------------------------------------
def test_filter_streamable_never_copies_network_identity():
    text = "# header\nExposureTime\t5000\nGevPersistentIPAddress\t2886729985\nDeviceUserID\tcam1\nGain\t3\n"
    assert filter_streamable(text) == "# header\nExposureTime\t5000\nGain\t3\n"


@pytest.fixture
def arena_pair(monkeypatch):
    infos = [fake_arena.device_info(serial="1", mac="aa"), fake_arena.device_info(serial="2", mac="bb")]
    sdk = fake_arena.install(monkeypatch, fake_arena.FakeSystem(infos))
    cams = [ArenaCamera(ArenaDeviceInfo.from_sdk(i)) for i in infos]
    for cam in cams:
        cam.connect()
    yield sdk, cams
    for cam in cams:
        cam.disconnect()


def test_arena_copy_uses_feature_streams(arena_pair):
    sdk, (source, target) = arena_pair
    src_nodes = sdk.system.devices["aa"].nodemap.nodes
    src_nodes["ExposureTime"].value = 12_345.0
    src_nodes["PixelFormat"].value = "Mono8"
    src_nodes["GevPersistentIPAddress"] = FakeNode(1)
    dst_nodes = sdk.system.devices["bb"].nodemap.nodes
    dst_nodes["GevPersistentIPAddress"] = FakeNode(2)

    target.import_settings(source.export_settings())
    assert dst_nodes["ExposureTime"].value == 12_345.0
    assert dst_nodes["PixelFormat"].value == "Mono8"
    assert dst_nodes["GevPersistentIPAddress"].value == 2  # network identity is never copied


def test_arena_import_refused_while_acquiring(arena_pair):
    _, (source, target) = arena_pair
    target.start_acquisition()
    with pytest.raises(InvalidStateError):
        target.import_settings(source.export_settings())


def test_arena_reset_loads_default_user_set(arena_pair):
    sdk, (camera, _) = arena_pair
    nodes = sdk.system.devices["aa"].nodemap.nodes
    nodes["UserSetSelector"] = FakeNode("UserSet1")
    nodes["UserSetLoad"] = load = FakeCommand(action=lambda: setattr(nodes["ExposureTime"], "value", 5000.0))
    nodes["ExposureTime"].value = 99.0
    camera.reset_settings()
    assert nodes["UserSetSelector"].value == "Default"
    assert load.executed == 1
    assert nodes["ExposureTime"].value == 5000.0


def test_arena_reset_without_user_sets_is_unsupported(arena_pair):
    _, (camera, _) = arena_pair
    with pytest.raises(UnsupportedFeatureError):
        camera.reset_settings()


# --- Property Grid buttons ---------------------------------------------------------
@pytest.fixture
def grid(manager, monkeypatch):
    shown = []
    monkeypatch.setattr(dialogs, "message", lambda title, text: shown.append((title, text)))
    dpg.create_context()
    for cid in ("A", "B"):
        manager.connect(cid)
    changed = []
    names = {"A": "Sim (SN-A)", "B": "Sim (SN-B)", "C": "Sim (SN-C)"}
    window = PropertyGridWindow("A", "Sim (SN-A)", FeatureService(manager), on_close=lambda cid: None,
                                state_of=manager.state, camera_names=lambda: names,
                                on_settings_changed=changed.extend)
    yield window, manager, changed
    dpg.destroy_context()


def run_job(window):
    assert wait_for(lambda: window._job_done is not None)
    window.update()
    assert not window.busy


def test_grid_apply_to_all_reports_and_refreshes_other_grids(grid):
    window, manager, changed = grid
    manager.camera("A").set_gain(9.0)
    window.apply_to_all()
    assert window.busy
    run_job(window)
    assert manager.camera("B").gain == 9.0
    assert changed == ["B"]
    assert dpg.get_value(window._message).startswith("Applied to 1 of 2 other camera(s)")


def test_grid_reset_to_defaults(grid):
    window, manager, _ = grid
    manager.camera("A").set_gain(9.0)
    window.reset_to_defaults()
    run_job(window)
    assert manager.camera("A").gain == 0.0
    assert dpg.get_value(window._message) == "Settings reset to the camera's defaults"
