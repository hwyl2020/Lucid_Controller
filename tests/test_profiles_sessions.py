import json
import time

import pytest

from app.cameras.camera_device import CameraError, Roi
from app.cameras.camera_manager import CameraManager
from app.cameras.simulator_camera import SimulatorCamera, SimulatorConfig
from app.models.camera_state import CameraState
from app.services.camera_control_service import CameraControlService
from app.services.profile_service import CameraSettings, ProfileService, SettingsApplier
from app.services.session_manager import SessionManager


@pytest.fixture
def env(tmp_path):
    manager = CameraManager(frame_timeout=0.1)
    manager.add_camera(SimulatorCamera(SimulatorConfig(camera_id="A", width=640, height=480, fps=50)))
    manager.add_camera(SimulatorCamera(SimulatorConfig(camera_id="B", width=320, height=240, fps=50, model="Other")))
    applier = SettingsApplier(manager, CameraControlService(manager))
    profiles = ProfileService(manager, applier, tmp_path / "profiles")
    sessions = SessionManager(manager, applier, tmp_path / "sessions")
    yield manager, applier, profiles, sessions
    manager.shutdown()


def configure_a(manager):
    manager.connect("A")
    cam = manager.camera("A")
    cam.set_pixel_format("RGB8")
    cam.set_roi(64, 40, 320, 200)
    cam.set_exposure(5000)
    cam.set_gain(6)
    cam.set_frame_rate(25)


def test_settings_round_trip_dict():
    settings = CameraSettings("Mono8", Roi(8, 2, 64, 64), 1000.0, 1.5, 30.0)
    assert CameraSettings.from_dict(settings.to_dict()) == settings


def test_profile_save_list_apply(env):
    manager, _, profiles, _ = env
    configure_a(manager)
    path = profiles.save("A", "Bright lab / day")
    assert path.name == "Bright_lab_day.json"
    assert profiles.list_profiles() == ["Bright lab / day"]

    # Change A, then restore from the profile while streaming.
    cam = manager.camera("A")
    cam.set_exposure(100)
    manager.start_streaming("A")
    warnings = profiles.apply("A", "Bright lab / day")
    assert warnings == []
    assert (cam.exposure, cam.gain, cam.frame_rate, cam.pixel_format) == (5000, 6, 25, "RGB8")
    assert cam.roi == Roi(64, 40, 320, 200)
    assert manager.state("A") is CameraState.ACQUIRING


def test_profile_on_other_model_clamps_and_warns(env):
    manager, _, profiles, _ = env
    configure_a(manager)
    profiles.save("A", "p")
    manager.connect("B")
    warnings = profiles.apply("B", "p")
    assert any("saved from a Simulator" in w for w in warnings)
    # ROI 64,40 320x200 does not fit a 320x240 sensor at x=64 -> clamped to fit
    roi = manager.camera("B").roi
    assert roi.x + roi.width <= 320 and roi.width >= 64


def test_capture_requires_connection(env):
    _, applier, profiles, _ = env
    with pytest.raises(CameraError, match="connect"):
        profiles.save("A", "x")


def test_missing_profile(env):
    manager, _, profiles, _ = env
    manager.connect("A")
    with pytest.raises(CameraError, match="Cannot read profile"):
        profiles.apply("A", "nope")


def test_session_save_and_restore(env):
    manager, _, _, sessions = env
    configure_a(manager)
    manager.start_streaming("A")
    path = sessions.save("Line 3", layout="2x1")
    doc = json.loads(path.read_text())
    assert doc["layout"] == "2x1" and doc["cameras"]["A"]["streaming"] is True
    assert doc["cameras"]["B"]["settings"] is None  # B was never connected

    # Simulate a fresh start: everything stopped and changed.
    manager.disconnect("A")
    result = sessions.load("Line 3")
    assert result.layout == "2x1" and result.started == ["A"]
    assert result.warnings == []
    assert manager.state("A") is CameraState.ACQUIRING
    cam = manager.camera("A")
    assert (cam.exposure, cam.pixel_format, cam.roi) == (5000, "RGB8", Roi(64, 40, 320, 200))
    assert sessions.list_sessions() == ["Line_3"]


def test_session_reports_missing_cameras(env, tmp_path):
    manager, _, _, sessions = env
    sessions.save("s", layout="2x2")
    doc_path = tmp_path / "sessions" / "s.json"
    doc = json.loads(doc_path.read_text())
    doc["cameras"]["GONE"] = {"model": "TRI122S-C", "streaming": True, "settings": None}
    doc_path.write_text(json.dumps(doc))
    result = sessions.load("s")
    assert any("GONE (TRI122S-C) is not connected" in w for w in result.warnings)
