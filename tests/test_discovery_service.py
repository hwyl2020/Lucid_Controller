"""Hot-plug discovery: cameras connected while the app runs are added without a restart."""

import time

import dearpygui.dearpygui as dpg
import pytest

from app.cameras.camera_device import CameraError
from app.cameras.camera_manager import CameraManager
from app.cameras.simulator_camera import SimulatorCamera, SimulatorConfig
from app.services.camera_status_service import CameraStatusService
from app.services.configuration import DEFAULT_CONFIG
from app.services.discovery_service import DiscoveryService
from app.services.network_service import NetworkService
from app.services.recording_service import RecordingService
from app.ui.camera_sidebar import CameraSidebar
from app.ui.multiview import MultiView


def sim(cid):
    return SimulatorCamera(SimulatorConfig(camera_id=cid, serial_number=f"SN-{cid}", width=160, height=120))


class Network:
    """The cameras currently plugged in; each scan returns fresh (unopened) camera objects."""

    def __init__(self, *ids):
        self.ids = list(ids)
        self.fail = False

    def scan(self):
        if self.fail:
            raise CameraError("Camera discovery failed: adapter down")
        return [sim(cid) for cid in self.ids]


def test_new_cameras_are_added_and_known_ones_left_alone():
    manager = CameraManager()
    manager.add_camera(sim("A"))
    manager.connect("A")
    net = Network("A")
    service = DiscoveryService(manager, net.scan)
    assert service.scan_once() == []
    net.ids += ["B", "C"]
    assert service.scan_once() == ["B", "C"]
    assert manager.camera_ids == ["A", "B", "C"]
    assert manager.camera("A").connected  # the open camera was not replaced
    net.ids = ["C"]  # A and B unplugged: their rows stay
    assert service.scan_once() == []
    assert manager.camera_ids == ["A", "B", "C"]
    manager.shutdown()


def test_discovery_failure_is_survived():
    manager = CameraManager()
    net = Network("A")
    net.fail = True
    service = DiscoveryService(manager, net.scan)
    assert service.scan_once() == []
    net.fail = False
    assert service.scan_once() == ["A"]


def test_background_thread_finds_a_camera_plugged_in_later():
    manager = CameraManager()
    net = Network()
    service = DiscoveryService(manager, net.scan, interval=0.05)
    service.start()
    try:
        time.sleep(0.1)
        assert manager.camera_ids == []
        net.ids.append("A")
        deadline = time.monotonic() + 2
        while not manager.camera_ids and time.monotonic() < deadline:
            time.sleep(0.02)
        assert manager.camera_ids == ["A"]
    finally:
        service.stop()


def test_scan_now_wakes_the_thread():
    manager = CameraManager()
    net = Network("A")
    service = DiscoveryService(manager, net.scan, interval=60)
    service.start()
    try:
        service.scan_now()
        deadline = time.monotonic() + 2
        while not manager.camera_ids and time.monotonic() < deadline:
            time.sleep(0.02)
        assert manager.camera_ids == ["A"]
    finally:
        service.stop()


@pytest.fixture
def ui(tmp_path):
    cfg = {**DEFAULT_CONFIG, "recording": {**DEFAULT_CONFIG["recording"], "directory": str(tmp_path / "rec")}}
    manager = CameraManager()
    dpg.create_context()
    with dpg.window():
        with dpg.group() as side:
            pass
        with dpg.group() as area:
            pass
    sidebar = CameraSidebar(side, manager, CameraStatusService(manager), RecordingService(manager, cfg),
                            NetworkService(manager), on_property_grid=lambda cid: None)
    view = MultiView(area, manager, "2x2")
    yield manager, sidebar, view
    manager.shutdown()
    dpg.destroy_context()


def test_sidebar_and_tiles_pick_up_hot_plugged_cameras(ui):
    manager, sidebar, view = ui
    assert sidebar.rows == {} and dpg.is_item_shown(sidebar._empty)
    manager.add_camera(sim("A"))
    sidebar.update()
    view.update()
    assert list(sidebar.rows) == ["A"] and sidebar.selected == "A"
    assert not dpg.is_item_shown(sidebar._empty)
    assert [v.camera_id for v in view._views] == ["A", None, None, None]
    manager.add_camera(sim("B"))
    sidebar.update()
    view.update()
    assert list(sidebar.rows) == ["A", "B"]
    assert dpg.get_item_label(sidebar._count) == "2"
    assert [v.camera_id for v in view._views] == ["A", "B", None, None]


# --- unplugged cameras leave the app ---------------------------------------------------------
def test_unplugged_idle_camera_is_reported_gone_after_missing_rounds():
    manager = CameraManager()
    net = Network("A", "B")
    service = DiscoveryService(manager, net.scan, missing_scans=2)
    service.scan_once()
    net.ids = ["A"]  # B unplugged
    service.scan_once()
    assert service.take_gone() == []  # one missed reply is not enough
    service.scan_once()
    assert service.take_gone() == ["B"]
    service.scan_once()
    assert service.take_gone() == []  # reported once


def test_camera_back_before_the_limit_is_kept():
    manager = CameraManager()
    net = Network("A")
    service = DiscoveryService(manager, net.scan, missing_scans=2)
    service.scan_once()
    net.ids = []
    service.scan_once()
    net.ids = ["A"]
    service.scan_once()
    net.ids = []
    service.scan_once()
    assert service.take_gone() == []


def test_streaming_camera_is_not_removed_on_a_missed_reply():
    manager = CameraManager(frame_timeout=0.1)
    net = Network("A")
    service = DiscoveryService(manager, net.scan, missing_scans=1)
    service.scan_once()
    manager.connect("A")
    manager.start_streaming("A")
    net.ids = []
    service.scan_once()
    assert service.take_gone() == []
    manager.stop_streaming("A")  # e.g. the stream failed after the cable was pulled
    service.scan_once()
    assert service.take_gone() == ["A"]
    manager.shutdown()


def test_cameras_not_found_by_discovery_are_never_removed():
    manager = CameraManager()
    manager.add_camera(sim("SIM"))  # e.g. a --simulators test camera
    service = DiscoveryService(manager, Network().scan, missing_scans=1)
    for _ in range(3):
        service.scan_once()
    assert service.take_gone() == []


def test_adopted_startup_camera_is_removed_when_unplugged():
    manager = CameraManager()
    manager.add_camera(sim("A"))
    service = DiscoveryService(manager, Network().scan, missing_scans=1)
    service.adopt(["A"])
    service.scan_once()
    assert service.take_gone() == ["A"]


def test_remove_camera_finishes_recording_and_closes_it(tmp_path):
    from app.services.app_services import AppServices
    cfg = {**DEFAULT_CONFIG}
    for key in ("recording", "snapshots", "profiles", "sessions", "diagnostics", "logging"):
        cfg[key] = {**DEFAULT_CONFIG[key], "directory": str(tmp_path / key)}
    manager = CameraManager(frame_timeout=0.1)
    camera = sim("A")
    manager.add_camera(camera)
    services = AppServices.create(cfg, tmp_path / "c.json", manager)
    manager.connect("A")
    manager.start_streaming("A")
    services.recording.start(camera_ids=["A"])
    time.sleep(0.3)
    services.remove_camera("A")
    assert manager.camera_ids == []  # gone from the app at once
    deadline = time.monotonic() + 5
    while (camera.connected or services.recording.is_recording("A")) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not camera.connected
    assert not services.recording.is_recording("A")
    services.remove_camera("A")  # already gone: no error


def test_sidebar_and_tiles_drop_unplugged_cameras(ui):
    manager, sidebar, view = ui
    for cid in ("A", "B"):
        manager.add_camera(sim(cid))
    sidebar.update()
    view.update()
    manager.detach_camera("A")
    sidebar.update()
    view.update()
    assert list(sidebar.rows) == ["B"] and sidebar.selected == "B"
    assert [v.camera_id for v in view._views] == [None, "B", None, None]
    manager.detach_camera("B")
    sidebar.update()
    view.update()
    assert sidebar.rows == {} and dpg.is_item_shown(sidebar._empty)
    assert dpg.get_item_label(sidebar._count) == "0"


def test_fullscreen_button_shows_one_camera_and_returns_to_the_grid(ui):
    manager, sidebar, view = ui
    for cid in ("A", "B"):
        manager.add_camera(sim(cid))
    view.update()
    assert [v.camera_id for v in view._views] == ["A", "B", None, None]
    view.toggle_focus("B")  # fullscreen button on B's tile
    assert [v.camera_id for v in view._views] == ["B"] and view._views[0].focused
    view.toggle_focus("B")  # again: back to the grid
    assert [v.camera_id for v in view._views] == ["A", "B", None, None]
    view.toggle_focus("A")
    view.set_layout("2x2")  # choosing a layout also leaves fullscreen
    assert view.focus is None and len(view._views) == 4
    view.toggle_focus("A")
    manager.detach_camera("A")  # the camera shown fullscreen is unplugged
    view.update()
    assert view.focus is None and [v.camera_id for v in view._views] == ["B", None, None, None]
