import time

import pytest

from app.acquisition.frame_queue import RecordingQueue
from app.cameras.camera_manager import CameraManager
from app.cameras.camera_device import CameraNotConnectedError
from app.cameras.simulator_camera import SimulatorCamera, SimulatorConfig
from app.models.camera_state import CameraState


def wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def manager():
    m = CameraManager(frame_timeout=0.1)
    for i in range(4):
        m.add_camera(SimulatorCamera(SimulatorConfig(camera_id=f"SIM-{i}", width=320, height=240, fps=100)))
    yield m
    m.shutdown()


def test_duplicate_camera_rejected(manager):
    with pytest.raises(ValueError):
        manager.add_camera(SimulatorCamera(SimulatorConfig(camera_id="SIM-0")))


def test_state_transitions(manager):
    assert manager.state("SIM-0") is CameraState.DISCONNECTED
    manager.connect("SIM-0")
    assert manager.state("SIM-0") is CameraState.CONNECTED
    manager.start_streaming("SIM-0")
    assert manager.state("SIM-0") is CameraState.ACQUIRING
    manager.stop_streaming("SIM-0")
    assert manager.state("SIM-0") is CameraState.CONNECTED
    manager.disconnect("SIM-0")
    assert manager.state("SIM-0") is CameraState.DISCONNECTED


def test_four_cameras_stream_independently(manager):
    for cid in manager.camera_ids:
        manager.connect(cid)
        manager.start_streaming(cid)

    for cid in manager.camera_ids:
        assert wait_for(lambda: (s := manager.stats(cid)) is not None and s.frames_acquired >= 5)
        frame = manager.latest_frame(cid)
        assert frame is not None and frame.camera_id == cid


def test_streaming_unconnected_camera_reports_error(manager):
    manager.start_streaming("SIM-1")
    assert wait_for(lambda: manager.state("SIM-1") is CameraState.ERROR)
    assert "not connected" in manager.last_error("SIM-1")


def test_disconnect_during_streaming_reports_error_without_crashing(manager):
    manager.connect("SIM-2")
    manager.start_streaming("SIM-2")
    assert wait_for(lambda: manager.latest_frame("SIM-2") is not None)

    manager.camera("SIM-2").simulate_disconnect()

    assert wait_for(lambda: manager.state("SIM-2") is CameraState.ERROR)
    assert "lost" in manager.last_error("SIM-2")

    # recover: reconnect clears the error and streaming resumes
    manager.stop_streaming("SIM-2")
    manager.connect("SIM-2")
    manager.start_streaming("SIM-2")
    assert manager.state("SIM-2") is CameraState.ACQUIRING
    assert wait_for(lambda: manager.latest_frame("SIM-2") is not None)


def test_recording_queue_receives_every_frame(manager):
    manager.connect("SIM-3")
    manager.start_streaming("SIM-3")
    recording = RecordingQueue(maxsize=1000)
    manager.set_recording_queue("SIM-3", recording)
    assert wait_for(lambda: recording.depth >= 10)
    manager.stop_streaming("SIM-3")

    ids = []
    while (frame := recording.get(timeout=0)) is not None:
        ids.append(frame.frame_id)
    assert ids == list(range(ids[0], ids[0] + len(ids)))  # contiguous, nothing dropped
    assert recording.overflow_count == 0


def test_setters_on_disconnected_camera_raise(manager):
    with pytest.raises(CameraNotConnectedError):
        manager.camera("SIM-0").set_exposure(1000)
