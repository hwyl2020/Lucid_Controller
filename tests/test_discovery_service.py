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
    assert dpg.get_value(sidebar._count) == "2"
    assert [v.camera_id for v in view._views] == ["A", "B", None, None]
