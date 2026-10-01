import time
from types import SimpleNamespace

import pytest

from app.cameras.camera_manager import CameraManager
from app.cameras.simulator_camera import SimulatorCamera, SimulatorConfig
from app.models.camera_state import CameraState
from app.services.performance_monitor import PerformanceMonitor, _nic_for_ip


def addr(address, netmask):
    return SimpleNamespace(address=address, netmask=netmask)


def test_nic_for_ip_matches_subnet():
    if_addrs = {
        "Wi-Fi": [addr("192.168.1.11", "255.255.255.0")],
        "Ethernet": [addr("fe80::1", None), addr("172.16.1.52", "255.255.255.0")],
    }
    assert _nic_for_ip("172.16.1.41", if_addrs) == "Ethernet"
    assert _nic_for_ip("10.0.0.5", if_addrs) is None
    assert _nic_for_ip("not-an-ip", if_addrs) is None


@pytest.fixture
def manager():
    m = CameraManager(frame_timeout=0.1)
    m.add_camera(SimulatorCamera(SimulatorConfig(camera_id="SIM", width=320, height=240, fps=50)))
    yield m
    m.shutdown()


def test_samples_camera_and_host(manager):
    monitor = PerformanceMonitor(manager, interval=0.2)
    manager.connect("SIM")
    manager.start_streaming("SIM")
    time.sleep(1.3)  # one full FPS window
    sample = monitor.sample()
    cam = sample.cameras[0].status
    assert cam.state is CameraState.ACQUIRING
    assert 30 < cam.fps < 70
    expected_mbps = cam.fps * 320 * 240 * 8 / 1e6  # Mono8 = 1 byte/pixel, shown in megabits
    assert cam.bandwidth_mbps == pytest.approx(expected_mbps, rel=0.3)
    assert cam.frames_missed == 0
    host = sample.host
    assert host.process_memory_mb > 10
    assert 0 <= host.memory_percent <= 100


def test_worker_counts_frame_gaps(manager, monkeypatch):
    camera = manager.camera("SIM")
    original = camera.get_frame

    def skipping_get_frame(timeout=1.0):
        frame = original(timeout)
        if frame.frame_id == 5:  # pretend frames 5..7 were lost in transport
            original(timeout)
            original(timeout)
            return original(timeout)
        return frame

    monkeypatch.setattr(camera, "get_frame", skipping_get_frame)
    manager.connect("SIM")
    manager.start_streaming("SIM")
    deadline = time.monotonic() + 2
    while (manager.stats("SIM").frames_acquired < 10) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert manager.stats("SIM").frames_missed == 3
