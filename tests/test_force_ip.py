"""Force IP: addressing rules, ArenaCamera via the fake SDK, adapter choice, and the two-click ON flow."""

import copy
import time

import dearpygui.dearpygui as dpg
import pytest

from app.cameras.arena_camera import ArenaCamera
from app.cameras.camera_device import CameraError, InvalidStateError
from app.cameras.camera_discovery import ArenaDeviceInfo
from app.cameras.camera_manager import CameraManager
from app.cameras.network import HostInterface, plan_force_ip, reachable_interface
from app.models.camera_state import CameraState
from app.services import network_service
from app.services.camera_status_service import CameraStatusService
from app.services.configuration import DEFAULT_CONFIG
from app.services.network_service import NetworkService
from app.services.recording_service import RecordingService
from app.ui.camera_sidebar import CameraSidebar
from tests import fake_arena
from tests.fake_arena import FakeSystem

ETHERNET = HostInterface("172.16.1.52", "255.255.255.0", "aa:00:00:00:00:01")
WIFI = HostInterface("192.168.1.11", "255.255.255.0", "bb:00:00:00:00:02")
LINK_LOCAL_CAMERA = "169.254.92.34"


# --- pure addressing rules ---------------------------------------------------------
def test_reachable_interface():
    assert reachable_interface("172.16.1.41", [WIFI, ETHERNET]) == ETHERNET
    assert reachable_interface(LINK_LOCAL_CAMERA, [WIFI, ETHERNET]) is None
    assert reachable_interface("bad", [ETHERNET]) is None


def test_plan_keeps_host_number_when_free():
    plan = plan_force_ip(LINK_LOCAL_CAMERA, ETHERNET, set())
    assert (plan.ip, plan.subnet_mask, plan.gateway) == ("172.16.1.34", "255.255.255.0", "0.0.0.0")


def test_plan_avoids_used_adapter_and_special_addresses():
    assert plan_force_ip("169.254.1.52", ETHERNET, set()).ip == "172.16.1.53"  # .52 is the adapter itself
    assert plan_force_ip(LINK_LOCAL_CAMERA, ETHERNET, {"172.16.1.34"}).ip == "172.16.1.53"
    assert plan_force_ip("169.254.0.255", ETHERNET, set()).ip == "172.16.1.53"  # .255 is broadcast
    assert plan_force_ip("10.0.0.0", ETHERNET, set()).ip == "172.16.1.53"  # .0 is the network address
    tiny = HostInterface("10.0.0.1", "255.255.255.252")  # hosts .1 (adapter) and .2
    assert plan_force_ip("1.2.3.4", tiny, set()).ip == "10.0.0.2"
    with pytest.raises(ValueError, match="No free address"):
        plan_force_ip("1.2.3.4", tiny, {"10.0.0.2"})


# --- ArenaCamera with the fake SDK -----------------------------------------------------
@pytest.fixture
def unreachable(monkeypatch):
    info = fake_arena.device_info(ip=LINK_LOCAL_CAMERA)
    info["subnetmask"] = "255.255.0.0"
    system = FakeSystem([info])
    system.interface_infos = [{"ip": ETHERNET.ip, "subnetmask": ETHERNET.subnet_mask, "mac": ETHERNET.mac}]
    sdk = fake_arena.install(monkeypatch, system)
    return sdk, ArenaCamera(ArenaDeviceInfo.from_sdk(info))


def test_network_check_detects_wrong_subnet(unreachable):
    _, cam = unreachable
    check = cam.network_check()
    assert not check.reachable and check.camera_ip == LINK_LOCAL_CAMERA
    assert check.interfaces == (ETHERNET,) and ETHERNET.ip in check.used_ips


def test_force_ip_moves_camera_and_updates_ip(unreachable):
    sdk, cam = unreachable
    check = cam.network_check()
    cam.force_ip(plan_force_ip(check.camera_ip, ETHERNET, set(check.used_ips)))
    assert sdk.system.forced[-1] == {"mac": cam.info.mac, "ip": "172.16.1.34", "subnetmask": "255.255.255.0",
                                     "defaultgateway": "0.0.0.0"}
    assert cam.ip_address == "172.16.1.34" and cam.network_check().reachable


def test_force_ip_reports_camera_that_does_not_move(unreachable):
    sdk, cam = unreachable
    sdk.system.ignore_force_ip = True
    plan = plan_force_ip(LINK_LOCAL_CAMERA, ETHERNET, set())
    with pytest.raises(CameraError, match="did not take IP"):
        cam.force_ip(plan, timeout_s=0.3)


def test_force_ip_refused_while_camera_open(monkeypatch):
    fake_arena.install(monkeypatch)
    cam = ArenaCamera(ArenaDeviceInfo.from_sdk(fake_arena.device_info()))
    cam.connect()
    with pytest.raises(InvalidStateError, match="turn the camera off"):
        cam.force_ip(plan_force_ip("169.254.1.10", HostInterface("169.254.1.1", "255.255.0.0"), set()))


# --- adapter choice ----------------------------------------------------------------------
def test_candidates_prefer_wired(monkeypatch):
    from app.cameras.camera_device import NetworkCheck

    check = NetworkCheck(LINK_LOCAL_CAMERA, "255.255.0.0", False, (WIFI, ETHERNET), frozenset())
    monkeypatch.setattr(network_service, "_wireless_macs", lambda: {WIFI.mac})
    assert NetworkService(CameraManager()).candidate_interfaces(check) == [ETHERNET]
    monkeypatch.setattr(network_service, "_wireless_macs", lambda: set())
    assert NetworkService(CameraManager()).candidate_interfaces(check) == [WIFI, ETHERNET]  # user chooses


# --- two-click ON flow through a CameraRow ------------------------------------------------
@pytest.fixture
def row(unreachable, tmp_path, monkeypatch):
    monkeypatch.setattr(network_service, "_wireless_macs", lambda: set())
    _, cam = unreachable
    manager = CameraManager(frame_timeout=0.1)
    manager.add_camera(cam)
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["recording"]["directory"] = str(tmp_path / "rec")
    recording = RecordingService(manager, cfg)
    dpg.create_context()
    with dpg.window():
        with dpg.group() as parent:
            pass
    bar = CameraSidebar(parent, manager, CameraStatusService(manager), recording, NetworkService(manager),
                        on_property_grid=lambda cid: None)
    yield bar.rows[cam.camera_id], manager, cam
    manager.shutdown()
    dpg.destroy_context()


def wait_idle(row, timeout=5.0):
    deadline = time.monotonic() + timeout
    while row.busy and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not row.busy


def test_first_click_forces_ip_second_click_turns_camera_on(row):
    camera_row, manager, cam = row
    camera_row.toggle_power()
    wait_idle(camera_row)
    assert not cam.connected and cam.ip_address == "172.16.1.34"
    text, is_error = camera_row._notice
    assert not is_error and "IP forced to 172.16.1.34" in text and "Click ON again" in text
    camera_row.toggle_power()
    wait_idle(camera_row)
    assert cam.connected and manager.state(cam.camera_id) is CameraState.CONNECTED  # on, not streaming
    assert camera_row._notice is None


def test_reachable_camera_opens_on_first_click(monkeypatch, tmp_path):
    fake_arena.install(monkeypatch)  # default fake: camera and adapter on 169.254.0.0/16
    cam = ArenaCamera(ArenaDeviceInfo.from_sdk(fake_arena.device_info()))
    manager = CameraManager()
    manager.add_camera(cam)
    service = NetworkService(manager)
    check = service.check(cam.camera_id)
    assert check.reachable
