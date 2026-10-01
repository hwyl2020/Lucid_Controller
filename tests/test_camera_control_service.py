import time

import pytest

from app.cameras.camera_device import InvalidValueError, NumericRange, Roi, RoiLimits, UnsupportedFeatureError
from app.cameras.camera_manager import CameraManager
from app.cameras.simulator_camera import SimulatorCamera, SimulatorConfig
from app.models.camera_state import CameraState
from app.services.camera_control_service import CameraControlService


@pytest.fixture
def setup():
    manager = CameraManager(frame_timeout=0.1)
    manager.add_camera(SimulatorCamera(SimulatorConfig(camera_id="SIM", width=640, height=480, fps=100)))
    service = CameraControlService(manager)
    yield manager, service
    manager.shutdown()


def wait_for_frame(manager, predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = manager.latest_frame("SIM")
        if frame is not None and predicate(frame):
            return frame
        time.sleep(0.01)
    return None


class TestClamp:
    def test_numeric_clamp(self):
        r = NumericRange(10, 100, increment=5)
        assert r.clamp(3) == 10
        assert r.clamp(1000) == 100
        assert r.clamp(23) == 25
        assert NumericRange(0, 1).clamp(0.5) == 0.5

    def test_numeric_clamp_never_exceeds_max(self):
        assert NumericRange(0, 10, increment=4).clamp(10) == 8

    def test_roi_clamp(self):
        limits = RoiLimits(640, 480, 64, 64, 8, 2, 8, 2)
        assert limits.clamp(Roi(5, 3, 1001, 99)) == Roi(0, 2, 640, 98)
        assert limits.clamp(Roi(600, 0, 100, 100)) == Roi(544, 0, 96, 100)  # 64 + 4*8; x pulled in to fit
        assert limits.clamp(Roi(0, 0, 10, 10)) == Roi(0, 0, 64, 64)
        for roi in (Roi(5, 3, 1001, 99), Roi(633, 477, 70, 70)):
            limits.validate(limits.clamp(roi))


def test_snapshot_disconnected(setup):
    _, service = setup
    snap = service.snapshot("SIM")
    assert not snap.connected and snap.exposure_range is None and snap.pixel_formats == []


def test_snapshot_connected(setup):
    manager, service = setup
    manager.connect("SIM")
    snap = service.snapshot("SIM")
    assert snap.connected and not snap.streaming
    assert snap.pixel_formats == ["Mono8", "RGB8"]
    assert snap.roi == Roi(0, 0, 640, 480)
    assert snap.frame_rate == 100


def test_numeric_setters_clamp_and_apply(setup):
    manager, service = setup
    manager.connect("SIM")
    assert service.set_exposure("SIM", 5) == 20.0  # below minimum -> minimum
    assert manager.camera("SIM").exposure == 20.0
    assert service.set_gain("SIM", 99) == 24.0
    assert service.set_frame_rate("SIM", 30) == 30.0


def test_pixel_format_change_while_streaming_restarts_stream(setup):
    manager, service = setup
    manager.connect("SIM")
    manager.start_streaming("SIM")
    service.set_pixel_format("SIM", "RGB8")
    assert manager.state("SIM") is CameraState.ACQUIRING
    assert wait_for_frame(manager, lambda f: f.pixel_format == "RGB8") is not None


def test_roi_change_while_streaming_restarts_stream(setup):
    manager, service = setup
    manager.connect("SIM")
    manager.start_streaming("SIM")
    applied = service.set_roi("SIM", Roi(3, 3, 333, 201))
    assert applied == Roi(0, 2, 328, 200)
    assert manager.state("SIM") is CameraState.ACQUIRING
    assert wait_for_frame(manager, lambda f: (f.width, f.height) == (328, 200)) is not None
    assert service.reset_roi("SIM") == Roi(0, 0, 640, 480)


def test_stream_resumes_even_if_setting_fails(setup):
    manager, service = setup
    manager.connect("SIM")
    manager.start_streaming("SIM")
    with pytest.raises(InvalidValueError):
        service.set_pixel_format("SIM", "BayerRG8")
    assert manager.state("SIM") is CameraState.ACQUIRING


def test_unsupported_numeric_feature(setup, monkeypatch):
    manager, service = setup
    manager.connect("SIM")
    monkeypatch.setattr(manager.camera("SIM"), "gain_range", lambda: None)
    with pytest.raises(UnsupportedFeatureError):
        service.set_gain("SIM", 1)
