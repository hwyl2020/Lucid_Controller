import time

import numpy as np
import pytest

from app.cameras.camera_device import (
    CameraDisconnectedError,
    CameraNotConnectedError,
    FrameTimeoutError,
    InvalidStateError,
    InvalidValueError,
)
from app.cameras.simulator_camera import PATTERNS, SimulatorCamera, SimulatorConfig


def make_camera(**overrides) -> SimulatorCamera:
    config = SimulatorConfig(width=320, height=240, fps=200.0, **overrides)
    return SimulatorCamera(config)


@pytest.fixture
def streaming_camera():
    cam = make_camera()
    cam.connect()
    cam.start_acquisition()
    yield cam
    cam.disconnect()


def test_identity_from_config():
    cam = make_camera(camera_id="CAM-7", serial_number="123")
    assert (cam.camera_id, cam.serial_number, cam.model) == ("CAM-7", "123", "Simulator")
    assert not cam.connected and not cam.acquiring


def test_invalid_config_rejected():
    with pytest.raises(ValueError):
        make_camera(pattern="nope")
    with pytest.raises(ValueError):
        make_camera(pixel_format="BayerRG8")


def test_get_frame_requires_acquisition():
    cam = make_camera()
    with pytest.raises(CameraNotConnectedError):
        cam.get_frame()
    cam.connect()
    with pytest.raises(InvalidStateError):
        cam.get_frame()


def test_frames_have_expected_shape_and_increasing_ids(streaming_camera):
    frames = [streaming_camera.get_frame() for _ in range(3)]
    assert [f.frame_id for f in frames] == [1, 2, 3]
    for f in frames:
        assert f.data.shape == (240, 320) and f.data.dtype == np.uint8
        assert (f.width, f.height, f.pixel_format) == (320, 240, "Mono8")
        assert f.camera_id == streaming_camera.camera_id


@pytest.mark.parametrize("pattern", PATTERNS)
def test_all_patterns_render(pattern):
    cam = make_camera(pattern=pattern)
    cam.connect()
    cam.start_acquisition()
    assert cam.get_frame().data.shape == (240, 320)


def test_rgb_pixel_format():
    cam = make_camera()
    cam.connect()
    cam.set_pixel_format("RGB8")
    cam.start_acquisition()
    frame = cam.get_frame()
    assert frame.pixel_format == "RGB8"
    assert frame.data.shape == (240, 320, 3)


def test_roi_crops_frame():
    cam = make_camera()
    cam.connect()
    cam.set_roi(64, 40, 128, 100)
    cam.start_acquisition()
    frame = cam.get_frame()
    assert (frame.width, frame.height) == (128, 100)
    assert frame.data.shape == (100, 128)


def test_pixel_format_and_roi_locked_while_acquiring(streaming_camera):
    with pytest.raises(InvalidStateError):
        streaming_camera.set_pixel_format("RGB8")
    with pytest.raises(InvalidStateError):
        streaming_camera.set_roi(0, 0, 64, 64)


def test_exposure_and_gain_validated_and_affect_brightness():
    cam = make_camera(pattern="gradient")
    cam.connect()
    with pytest.raises(InvalidValueError):
        cam.set_exposure(1)
    with pytest.raises(InvalidValueError):
        cam.set_gain(100)
    cam.start_acquisition()
    base = cam.get_frame().data[100:, :].mean()
    cam.set_exposure(cam.REFERENCE_EXPOSURE_US / 2)
    darker = cam.get_frame().data[100:, :].mean()
    assert darker < base * 0.7


def test_frame_rate_pacing():
    cam = make_camera()
    cam.connect()
    cam.set_frame_rate(50)
    cam.start_acquisition()
    start = time.perf_counter()
    for _ in range(10):
        cam.get_frame()
    elapsed = time.perf_counter() - start
    assert 0.15 <= elapsed < 1.0  # 9 periods of 20 ms after the immediate first frame


def test_timeout_when_next_frame_not_due():
    cam = make_camera()
    cam.connect()
    cam.set_frame_rate(1)
    cam.start_acquisition()
    cam.get_frame()
    with pytest.raises(FrameTimeoutError):
        cam.get_frame(timeout=0.05)


def test_simulated_disconnect_and_reconnect(streaming_camera):
    streaming_camera.simulate_disconnect()
    assert not streaming_camera.connected
    with pytest.raises(CameraDisconnectedError):
        streaming_camera.get_frame()
    streaming_camera.connect()
    streaming_camera.start_acquisition()
    assert streaming_camera.get_frame().frame_id == 1


def test_capabilities():
    cam = make_camera()
    assert cam.pixel_formats() == ["Mono8", "RGB8"]
    assert cam.roi_limits().full_frame().width == 320
    assert cam.exposure_range() is not None
