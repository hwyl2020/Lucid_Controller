import logging
import threading
import time

import pytest

from app.cameras.camera_manager import CameraManager
from app.cameras.simulator_camera import SimulatorCamera, SimulatorConfig
from app.models.camera_state import CameraState
from app.models.units import bytes_per_second_to_mbps, format_mbps
from app.services.camera_status_service import CameraStatusService
from app.services.log_buffer import LogBuffer


def test_mbps_conversion_and_format():
    assert bytes_per_second_to_mbps(125_000_000) == 1000.0  # 125 MB/s of bytes = 1 Gb/s
    assert format_mbps(985.44) == "985.4 Mb/s"
    assert format_mbps(1842.6) == "1,842.6 Mb/s"


@pytest.fixture
def manager():
    m = CameraManager(frame_timeout=0.1)
    m.add_camera(SimulatorCamera(SimulatorConfig(camera_id="SIM-1", serial_number="230800123", model="TRI023S-CC",
                                                 ip_address="192.168.1.101", width=320, height=240, fps=50)))
    m.add_camera(SimulatorCamera(SimulatorConfig(camera_id="SIM-2", serial_number="240100456", ip_address=None)))
    yield m
    m.shutdown()


def test_status_identity_and_idle_values(manager):
    status = CameraStatusService(manager).status("SIM-1")
    assert status.display_name == "TRI023S-CC (230800123)"
    assert status.ip_address == "192.168.1.101"
    assert status.state is CameraState.DISCONNECTED and not status.connected
    assert (status.fps, status.bandwidth_mbps, status.frame_count) == (0.0, 0.0, 0)
    assert CameraStatusService(manager).status("SIM-2").ip_address is None


def test_status_while_streaming(manager):
    manager.connect("SIM-1")
    manager.start_streaming("SIM-1")
    time.sleep(1.3)
    status = CameraStatusService(manager).status("SIM-1")
    assert status.acquiring and status.connected
    assert status.frame_count > 30 and 30 < status.fps < 70
    assert status.bandwidth_mbps == pytest.approx(status.fps * 320 * 240 * 8 / 1e6, rel=0.3)


def test_log_buffer_incremental_and_bounded():
    buffer = LogBuffer(capacity=3)
    logger = logging.getLogger("test.logbuffer")
    logger.addHandler(buffer)
    logger.setLevel(logging.DEBUG)
    try:
        logger.info("one")
        first = buffer.since(0)
        assert [e.message for e in first] == ["one"] and first[0].level == "INFO"
        for text in ("two", "three", "four", "five"):
            logger.warning(text)
        assert [e.message for e in buffer.entries()] == ["three", "four", "five"]  # oldest dropped
        assert [e.message for e in buffer.since(first[0].seq)] == ["three", "four", "five"]
        assert buffer.since(buffer.last_seq) == []
        buffer.clear()
        assert buffer.entries() == []
        logger.error("six")
        assert [e.message for e in buffer.since(0)] == ["six"]  # seq keeps increasing after clear
    finally:
        logger.removeHandler(buffer)


def test_log_buffer_thread_safe():
    buffer = LogBuffer(capacity=10_000)
    logger = logging.getLogger("test.logbuffer.threads")
    logger.addHandler(buffer)
    logger.setLevel(logging.INFO)
    try:
        threads = [threading.Thread(target=lambda: [logger.info("x") for _ in range(500)]) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        seqs = [e.seq for e in buffer.entries()]
        assert len(seqs) == 2000 and seqs == sorted(set(seqs))
    finally:
        logger.removeHandler(buffer)


def test_log_buffer_records_exceptions():
    buffer = LogBuffer()
    logger = logging.getLogger("test.logbuffer.exc")
    logger.addHandler(buffer)
    try:
        try:
            raise ValueError("bad value")
        except ValueError:
            logger.exception("failed")
        assert buffer.entries()[-1].message == "failed (ValueError: bad value)"
    finally:
        logger.removeHandler(buffer)
