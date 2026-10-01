import time

import pytest

from app.acquisition.frame_queue import RecordingQueue
from app.cameras.camera_device import CameraDisconnectedError
from app.cameras.camera_manager import CameraManager
from app.cameras.simulator_camera import SimulatorCamera, SimulatorConfig
from app.models.camera_state import CameraState
from app.services.reconnect_service import ReconnectService


def wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def setup():
    manager = CameraManager(frame_timeout=0.1)
    camera = SimulatorCamera(SimulatorConfig(camera_id="SIM", width=160, height=120, fps=100))
    manager.add_camera(camera)
    service = ReconnectService(manager, initial_delay=0.0, max_delay=0.4, poll_interval=0.02)
    yield manager, camera, service
    service.stop()
    manager.shutdown()


def lose_camera(manager, camera):
    manager.connect("SIM")
    manager.start_streaming("SIM")
    assert wait_until(lambda: manager.latest_frame("SIM") is not None)
    camera.simulate_disconnect()
    assert wait_until(lambda: manager.state("SIM") is CameraState.ERROR)
    assert isinstance(manager.last_exception("SIM"), CameraDisconnectedError)


def test_reconnects_and_resumes_streaming(setup):
    manager, camera, service = setup
    lose_camera(manager, camera)
    service.start()
    assert wait_until(lambda: manager.state("SIM") is CameraState.ACQUIRING)
    assert manager.last_error("SIM") is None
    assert service.state("SIM") is None


def test_backoff_while_camera_stays_unavailable(setup, monkeypatch):
    manager, camera, service = setup
    lose_camera(manager, camera)
    calls = []

    def still_gone():
        calls.append(time.monotonic())
        raise CameraDisconnectedError("SIM: not found on the network")

    monkeypatch.setattr(camera, "connect", still_gone)
    for _ in range(4):
        service.poll_once()
        time.sleep(0.01)
    state = service.state("SIM")
    assert state.attempts == 1  # second attempt waits for the backoff delay
    assert "not found" in state.last_failure and state.next_attempt_in > 0
    assert manager.state("SIM") is CameraState.ERROR


def test_no_reconnect_after_user_stop(setup):
    manager, camera, service = setup
    lose_camera(manager, camera)
    manager.stop_streaming("SIM")  # user pressed Stop on the failed camera
    service.poll_once()
    assert service.state("SIM") is None


def test_no_reconnect_for_non_disconnect_errors(setup):
    manager, _, service = setup
    manager.start_streaming("SIM")  # never connected -> CameraNotConnectedError
    assert wait_until(lambda: manager.state("SIM") is CameraState.ERROR)
    service.poll_once()
    assert service.state("SIM") is None


def test_recording_resumes_after_reconnect(setup):
    manager, camera, service = setup
    queue = RecordingQueue(1000)
    manager.set_recording_queue("SIM", queue)
    lose_camera(manager, camera)
    depth_at_loss = queue.depth
    service.start()
    assert wait_until(lambda: queue.depth > depth_at_loss + 5)


def test_disabled_service_does_nothing(setup):
    manager, camera, service = setup
    lose_camera(manager, camera)
    service.enabled = False
    service.poll_once()
    assert manager.state("SIM") is CameraState.ERROR
