import time

import numpy as np
import pytest

from app.cameras import arena_sdk
from app.cameras.arena_camera import ArenaCamera
from app.cameras.camera_device import (
    CameraDisconnectedError,
    CameraError,
    CameraNotConnectedError,
    FrameTimeoutError,
    IncompleteFrameError,
    InvalidStateError,
    InvalidValueError,
    UnsupportedFeatureError,
)
from app.cameras.camera_discovery import ArenaDeviceInfo, discover_arena_cameras
from app.cameras.camera_manager import CameraManager
from app.models.camera_state import CameraState
from tests import fake_arena
from tests.fake_arena import FakeBuffer, FakeSystem, PixelFormat


@pytest.fixture
def sdk(monkeypatch):
    return fake_arena.install(monkeypatch)


@pytest.fixture
def camera(sdk):
    info = ArenaDeviceInfo.from_sdk(fake_arena.device_info())
    cam = ArenaCamera(info)
    cam.connect()
    yield cam
    cam.disconnect()


def device_of(sdk):
    return next(iter(sdk.system.devices.values()))


def mono8(frame_id=1, h=4, w=6, **kw):
    data = np.arange(h * w, dtype=np.uint8).reshape(h, w)
    return FakeBuffer(data, PixelFormat.Mono8, frame_id, 8, **kw), data


# --- discovery -------------------------------------------------------------
def test_discovery_maps_device_infos(sdk):
    infos = discover_arena_cameras(timeout_ms=250)
    assert sdk.system.DEVICE_INFOS_TIMEOUT_MILLISEC == 250
    assert len(infos) == 1
    info = infos[0]
    assert (info.model, info.serial, info.ip, info.firmware) == ("TRI051S-C", "224500001", "169.254.1.10", "1.80.0.0")


def test_discovery_without_sdk_returns_empty(monkeypatch):
    def unavailable():
        raise arena_sdk.ArenaSdkUnavailable("no sdk")

    monkeypatch.setattr(arena_sdk, "load", unavailable)
    assert discover_arena_cameras() == []


# --- connection --------------------------------------------------------------
def test_identity_from_info(sdk):
    cam = ArenaCamera(ArenaDeviceInfo.from_sdk(fake_arena.device_info()))
    assert (cam.camera_id, cam.model, cam.ip_address) == ("224500001", "TRI051S-C", "169.254.1.10")
    assert not cam.connected


def test_connect_applies_stream_settings(camera, sdk):
    stream = device_of(sdk).tl_stream_nodemap.nodes
    assert stream["StreamBufferHandlingMode"].value == "OldestFirst"
    assert stream["StreamAutoNegotiatePacketSize"].value is True
    assert stream["StreamPacketResendEnable"].value is True
    assert camera.connected


def test_connect_matches_by_mac_not_list_order(monkeypatch):
    other = fake_arena.device_info(serial="999", mac="aa:aa:aa:aa:aa:aa")
    target = fake_arena.device_info()
    sdk = fake_arena.install(monkeypatch, FakeSystem([other, target]))
    cam = ArenaCamera(ArenaDeviceInfo.from_sdk(target))
    cam.connect()
    assert list(sdk.system.devices) == [target["mac"]]


def test_connect_camera_not_on_network(monkeypatch):
    fake_arena.install(monkeypatch, FakeSystem([]))
    cam = ArenaCamera(ArenaDeviceInfo.from_sdk(fake_arena.device_info()))
    with pytest.raises(CameraDisconnectedError, match="not found"):
        cam.connect()


def test_access_denied_is_explained(sdk):
    sdk.system.create_error = Exception("Arena ERROR : ... ArenaC ERROR : ACCESS_DENIED -1005")
    cam = ArenaCamera(ArenaDeviceInfo.from_sdk(fake_arena.device_info()))
    with pytest.raises(CameraError, match="another application"):
        cam.connect()


def test_disconnect_destroys_device(camera, sdk):
    device = device_of(sdk)
    camera.disconnect()
    assert sdk.system.destroyed == [device]
    assert not camera.connected


# --- acquisition -------------------------------------------------------------
def test_get_frame_before_start(camera):
    with pytest.raises(InvalidStateError):
        camera.get_frame()


def test_get_frame_copies_and_requeues(camera, sdk):
    device = device_of(sdk)
    buffer, expected = mono8(frame_id=7)
    device.buffers.append(buffer)
    camera.start_acquisition()
    assert device.nodemap.nodes["AcquisitionMode"].value == "Continuous"

    frame = camera.get_frame(timeout=0.5)

    assert device.requeued == [buffer]
    np.testing.assert_array_equal(frame.data, expected)  # survived scribble after requeue
    assert (frame.frame_id, frame.pixel_format, frame.width, frame.height) == (7, "Mono8", 6, 4)
    assert frame.camera_id == "224500001"


def test_row_padding_is_removed(camera, sdk):
    buffer, expected = mono8(padding_x=4)
    device_of(sdk).buffers.append(buffer)
    camera.start_acquisition()
    np.testing.assert_array_equal(camera.get_frame().data, expected)


def test_mono12_kept_as_uint16(camera, sdk):
    data = np.array([[0, 4095], [100, 2048]], dtype=np.uint16)
    device_of(sdk).buffers.append(FakeBuffer(data, PixelFormat.Mono12, 1, 16))
    camera.start_acquisition()
    frame = camera.get_frame()
    assert frame.data.dtype == np.uint16 and frame.pixel_format == "Mono12"
    np.testing.assert_array_equal(frame.data, data)


def test_bayer_passed_through_raw(camera, sdk):
    data = np.arange(12, dtype=np.uint8).reshape(3, 4)
    device_of(sdk).buffers.append(FakeBuffer(data, PixelFormat.BayerRG8, 1, 8))
    camera.start_acquisition()
    frame = camera.get_frame()
    assert frame.pixel_format == "BayerRG8" and frame.data.shape == (3, 4)
    np.testing.assert_array_equal(frame.data, data)
    assert sdk.factory.converted == []


def test_packed_format_converted_by_sdk_and_destroyed(camera, sdk):
    data = np.arange(12, dtype=np.uint8).reshape(3, 4)
    device_of(sdk).buffers.append(FakeBuffer(data, PixelFormat.Mono10p, 1, 10))
    camera.start_acquisition()
    frame = camera.get_frame()
    assert frame.pixel_format == "Mono8" and frame.data.shape == (3, 4)
    assert sdk.factory.destroyed == sdk.factory.converted and len(sdk.factory.converted) == 1


def test_incomplete_frame_discarded_and_requeued(camera, sdk):
    device = device_of(sdk)
    buffer, _ = mono8(incomplete=True)
    device.buffers.append(buffer)
    camera.start_acquisition()
    with pytest.raises(IncompleteFrameError):
        camera.get_frame()
    assert device.requeued == [buffer]
    assert camera.incomplete_frames == 1


def test_timeout(camera):
    camera.start_acquisition()
    with pytest.raises(FrameTimeoutError):
        camera.get_frame(timeout=0.01)


def test_timeout_on_lost_device_reports_disconnect(camera, sdk):
    camera.start_acquisition()
    device_of(sdk).connected = False
    with pytest.raises(CameraDisconnectedError):
        camera.get_frame(timeout=0.01)


def test_frame_id_gaps_counted(camera, sdk):
    device = device_of(sdk)
    for frame_id in (1, 2, 5):
        device.buffers.append(mono8(frame_id)[0])
    camera.start_acquisition()
    for _ in range(3):
        camera.get_frame()
    assert camera.missed_frames == 2


def test_start_failure_translated(camera, sdk):
    device_of(sdk).start_error = Exception("ArenaC ERROR : ACCESS_DENIED -1005")
    with pytest.raises(CameraError, match="access denied"):
        camera.start_acquisition()
    assert not camera.acquiring


# --- capabilities & controls -------------------------------------------------
def test_pixel_formats_only_available_entries(camera):
    assert camera.pixel_formats() == ["Mono8", "BayerRG8", "RGB8"]


def test_ranges_and_values(camera):
    assert camera.exposure_range().maximum == 2_000_000.0
    assert camera.frame_rate_range().maximum == 24.6
    assert camera.exposure == 5000.0
    assert camera.pixel_format == "BayerRG8"
    limits = camera.roi_limits()
    assert (limits.sensor_width, limits.width_increment, limits.min_height) == (2448, 8, 2)


def test_missing_optional_node_reports_unsupported(sdk):
    nodes = fake_arena.default_nodes()
    del nodes["Gain"]
    sdk.system.devices["1c:0f:af:00:00:01"] = fake_arena.FakeDevice(nodes)
    cam = ArenaCamera(ArenaDeviceInfo.from_sdk(fake_arena.device_info()))
    cam.connect()
    assert cam.gain_range() is None and cam.gain is None
    with pytest.raises(UnsupportedFeatureError):
        cam.set_gain(1.0)


def test_set_exposure_turns_auto_off_and_validates(camera, sdk):
    nodes = device_of(sdk).nodemap.nodes
    camera.set_exposure(10_000)
    assert nodes["ExposureAuto"].value == "Off"
    assert nodes["ExposureTime"].value == 10_000.0
    with pytest.raises(InvalidValueError):
        camera.set_exposure(5)


def test_set_frame_rate_enables_control(camera, sdk):
    nodes = device_of(sdk).nodemap.nodes
    camera.set_frame_rate(10)
    assert nodes["AcquisitionFrameRateEnable"].value is True
    assert nodes["AcquisitionFrameRate"].value == 10.0


def test_set_pixel_format(camera, sdk):
    camera.set_pixel_format("Mono8")
    assert device_of(sdk).nodemap.nodes["PixelFormat"].value == "Mono8"
    with pytest.raises(InvalidValueError):
        camera.set_pixel_format("BayerRG16")  # listed by the camera but not available


def test_set_roi_writes_offsets_last(camera, sdk):
    nodes = device_of(sdk).nodemap.nodes
    camera.set_roi(64, 10, 1024, 512)
    assert nodes["OffsetX"].writes == [0, 64]
    assert (nodes["Width"].value, nodes["Height"].value, nodes["OffsetY"].value) == (1024, 512, 10)
    with pytest.raises(InvalidValueError):
        camera.set_roi(0, 0, 1001, 512)  # width not on increment


def test_settings_locked_while_acquiring(camera):
    camera.start_acquisition()
    with pytest.raises(InvalidStateError):
        camera.set_pixel_format("Mono8")
    with pytest.raises(InvalidStateError):
        camera.set_roi(0, 0, 64, 64)


def test_setter_when_not_connected(sdk):
    cam = ArenaCamera(ArenaDeviceInfo.from_sdk(fake_arena.device_info()))
    with pytest.raises(CameraNotConnectedError):
        cam.set_exposure(1000)


# --- end to end through the manager -----------------------------------------
def test_streams_through_camera_manager(sdk):
    cam = ArenaCamera(ArenaDeviceInfo.from_sdk(fake_arena.device_info()))
    manager = CameraManager(frame_timeout=0.05)
    manager.add_camera(cam)
    manager.connect(cam.camera_id)
    device = device_of(sdk)
    for i in range(1, 6):
        device.buffers.append(mono8(frame_id=i)[0])
    manager.start_streaming(cam.camera_id)
    deadline = time.monotonic() + 2
    while (manager.stats(cam.camera_id).frames_acquired < 5) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert manager.stats(cam.camera_id).frames_acquired == 5
    assert manager.latest_frame(cam.camera_id).frame_id == 5

    device.connected = False  # cable pulled
    deadline = time.monotonic() + 2
    while manager.state(cam.camera_id) is not CameraState.ERROR and time.monotonic() < deadline:
        time.sleep(0.01)
    assert manager.state(cam.camera_id) is CameraState.ERROR
    assert "lost" in manager.last_error(cam.camera_id)
    manager.shutdown()


def test_arena_camera_auto_reconnects_after_cable_pull(sdk):
    from app.services.reconnect_service import ReconnectService

    cam = ArenaCamera(ArenaDeviceInfo.from_sdk(fake_arena.device_info()))
    manager = CameraManager(frame_timeout=0.05)
    manager.add_camera(cam)
    manager.connect(cam.camera_id)
    manager.start_streaming(cam.camera_id)
    old_device = device_of(sdk)
    old_device.connected = False  # cable pulled
    deadline = time.monotonic() + 2
    while manager.state(cam.camera_id) is not CameraState.ERROR and time.monotonic() < deadline:
        time.sleep(0.01)
    assert manager.state(cam.camera_id) is CameraState.ERROR

    sdk.system.devices.clear()  # cable back in: the SDK hands out a fresh device handle
    service = ReconnectService(manager, initial_delay=0.0, poll_interval=0.02)
    service.poll_once()
    assert manager.state(cam.camera_id) is CameraState.ACQUIRING
    assert old_device in sdk.system.destroyed  # stale handle released
    new_device = device_of(sdk)
    assert new_device is not old_device and new_device.streaming
    manager.shutdown()
