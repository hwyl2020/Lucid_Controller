import json
import time

import cv2
import numpy as np
import pytest

from app.acquisition.frame import Frame
from app.acquisition.frame_queue import RecordingQueue
from app.cameras.camera_manager import CameraManager
from app.cameras.simulator_camera import SimulatorCamera, SimulatorConfig
from app.recording.recorder import CameraRecorder, RawSequenceWriter, RecordingMode, read_raw_sequence
from app.recording.snapshot import save_snapshot
from app.recording.video_writer import VideoFileWriter
from app.services.configuration import DEFAULT_CONFIG
from app.services.recording_service import RecordingError, RecordingService


def frame(frame_id, data=None, fmt="Mono8", camera_id="CAM"):
    data = np.full((6, 8), frame_id % 256, np.uint8) if data is None else data
    return Frame(camera_id, frame_id, 1700000000.0 + frame_id, data.shape[1], data.shape[0], fmt, data)


def wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# --- raw writer / recorder ----------------------------------------------------
def test_raw_sequence_round_trip(tmp_path):
    writer = RawSequenceWriter(tmp_path / "Camera_01")
    frames = [
        frame(1),
        frame(2, np.arange(48, dtype=np.uint16).reshape(6, 8), "BayerRG12"),
        frame(3, np.zeros((4, 5, 3), np.uint8), "RGB8"),
    ]
    for f in frames:
        writer.write(f)
    writer.close()
    read = list(read_raw_sequence(tmp_path / "Camera_01"))
    assert [f.frame_id for f in read] == [1, 2, 3]
    for original, loaded in zip(frames, read):
        assert loaded.pixel_format == original.pixel_format
        assert loaded.timestamp == pytest.approx(original.timestamp)
        np.testing.assert_array_equal(loaded.data, original.data)


def test_recorder_counts_frames_and_gaps(tmp_path):
    queue = RecordingQueue(16)
    recorder = CameraRecorder("CAM", RawSequenceWriter(tmp_path), queue)
    recorder.start()
    for fid in (1, 2, 3, 6, 7, 1, 2):  # 6 follows 3: 2 missing; then ids restart (stream restart)
        queue.put(frame(fid))
    stats = recorder.stop()
    assert stats.frames_written == 7
    assert stats.frame_gaps == 2
    assert stats.bytes_written == 7 * 48
    assert len(list(read_raw_sequence(tmp_path))) == 7


def test_recorder_flushes_queue_on_stop(tmp_path):
    queue = RecordingQueue(100)
    for fid in range(1, 51):
        queue.put(frame(fid))
    recorder = CameraRecorder("CAM", RawSequenceWriter(tmp_path), queue)
    recorder.start()
    assert recorder.stop().frames_written == 50


def test_recorder_reports_write_errors(tmp_path):
    class FailingWriter:
        closed = False

        def write(self, f):
            raise OSError(28, "No space left on device")

        def close(self):
            FailingWriter.closed = True

    queue = RecordingQueue(4)
    recorder = CameraRecorder("CAM", FailingWriter(), queue)
    recorder.start()
    queue.put(frame(1))
    assert wait_until(lambda: not recorder.running)
    assert "No space left" in recorder.stats().error
    assert FailingWriter.closed


# --- video writer -------------------------------------------------------------
def test_video_writer_half_resolution_mp4(tmp_path):
    writer = VideoFileWriter(tmp_path, fps=10)
    raw = np.random.default_rng(0).integers(0, 256, (120, 160), dtype=np.uint8)
    for fid in range(1, 6):
        writer.write(frame(fid, raw, "BayerRG8"))
    writer.close()
    capture = cv2.VideoCapture(str(tmp_path / "video.mp4"))
    assert capture.isOpened()
    assert (capture.get(cv2.CAP_PROP_FRAME_WIDTH), capture.get(cv2.CAP_PROP_FRAME_HEIGHT)) == (80, 60)
    assert int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) == 5
    capture.release()
    assert len((tmp_path / "frames.csv").read_text().splitlines()) == 6


def test_video_writer_rejects_size_change(tmp_path):
    writer = VideoFileWriter(tmp_path, fps=10)
    writer.write(frame(1, np.zeros((40, 40), np.uint8)))
    with pytest.raises(ValueError, match="size changed"):
        writer.write(frame(2, np.zeros((20, 40), np.uint8)))
    writer.close()


# --- snapshot -----------------------------------------------------------------
def test_snapshot_bayer_writes_raw_processed_and_metadata(tmp_path):
    raw = np.random.default_rng(1).integers(0, 256, (32, 48), dtype=np.uint8)
    files = save_snapshot(frame(9, raw, "BayerRG8"), tmp_path, {"camera": {"model": "X"}})
    np.testing.assert_array_equal(cv2.imread(str(files.raw), cv2.IMREAD_UNCHANGED), raw)
    assert cv2.imread(str(files.processed)).shape == (32, 48, 3)
    meta = json.loads(files.metadata.read_text())
    assert meta["camera"]["model"] == "X"
    assert meta["frame"]["pixel_format"] == "BayerRG8" and meta["frame"]["frame_id"] == 9


def test_snapshot_16bit_raw_is_lossless(tmp_path):
    raw = np.array([[0, 4095], [1234, 65535]], np.uint16)
    files = save_snapshot(frame(1, raw, "Mono16"), tmp_path, {})
    np.testing.assert_array_equal(cv2.imread(str(files.raw), cv2.IMREAD_UNCHANGED), raw)


# --- recording service with simulators -----------------------------------------
@pytest.fixture
def service(tmp_path):
    manager = CameraManager(frame_timeout=0.1)
    for i in (1, 2):
        manager.add_camera(SimulatorCamera(SimulatorConfig(camera_id=f"SIM-{i}", width=160, height=120, fps=60)))
    config = {
        **DEFAULT_CONFIG,
        "recording": {**DEFAULT_CONFIG["recording"], "directory": str(tmp_path / "rec"), "min_free_gb": 0,
                      "save_metadata": True},  # most tests inspect session.json
        "snapshots": {"directory": str(tmp_path / "snap")},
    }
    svc = RecordingService(manager, config)
    yield manager, svc
    svc.stop()
    manager.shutdown()


def stream_all(manager):
    for cid in manager.camera_ids:
        manager.connect(cid)
        manager.start_streaming(cid)
    assert wait_until(lambda: all(manager.snapshot_frame(c) is not None for c in manager.camera_ids))


def test_start_requires_streaming_camera(service):
    _, svc = service
    with pytest.raises(RecordingError, match="start a camera"):
        svc.start()


def test_raw_session_layout_and_contents(service):
    manager, svc = service
    stream_all(manager)
    session_dir = svc.start(RecordingMode.RAW)
    assert svc.is_recording("SIM-1") and svc.status().active
    assert wait_until(lambda: svc.status().frames_written >= 20)
    status = svc.stop()

    assert session_dir.name.startswith("Session_") and session_dir.parent.name.count("-") == 2
    doc = json.loads((session_dir / "session.json").read_text())
    assert doc["session"]["mode"] == "raw" and doc["session"]["stopped"]
    assert [c["directory"] for c in doc["cameras"]] == ["Camera_01", "Camera_02"]
    assert doc["cameras"][0]["model"] == "Simulator" and doc["cameras"][0]["pixel_format"] == "Mono8"
    for cam in doc["cameras"]:
        frames = list(read_raw_sequence(session_dir / cam["directory"]))
        assert len(frames) == cam["recording"]["frames_written"] > 0
        ids = [f.frame_id for f in frames]
        assert ids == list(range(ids[0], ids[0] + len(ids)))  # nothing lost between camera and disk
    assert status.dropped == 0
    assert not svc.active


def test_recording_survives_stream_restart(service):
    from app.services.camera_control_service import CameraControlService

    manager, svc = service
    stream_all(manager)
    session_dir = svc.start(RecordingMode.RAW, ["SIM-1"])
    assert wait_until(lambda: svc.status().frames_written >= 5)
    CameraControlService(manager).set_pixel_format("SIM-1", "RGB8")  # pauses + resumes the stream
    assert wait_until(lambda: any(
        f.pixel_format == "RGB8" for f in read_raw_sequence_safe(session_dir / "Camera_01")
    ) or svc.status().cameras["SIM-1"].frames_written > 40)
    svc.stop()
    formats = {f.pixel_format for f in read_raw_sequence(session_dir / "Camera_01")}
    assert formats == {"Mono8", "RGB8"}


def read_raw_sequence_safe(directory):
    try:
        return list(read_raw_sequence(directory))
    except Exception:  # noqa: BLE001 - file still being written
        return []


def test_video_session(service):
    manager, svc = service
    stream_all(manager)
    session_dir = svc.start(RecordingMode.VIDEO, ["SIM-2"])
    assert wait_until(lambda: svc.status().frames_written >= 10)
    svc.stop()
    capture = cv2.VideoCapture(str(session_dir / "Camera_01" / "video.mp4"))
    assert capture.isOpened() and int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) >= 10
    capture.release()


def test_video_session_without_metadata_is_just_the_video(service):
    """Default (save_metadata off): video.mp4 only, and the size shown is the real file size."""
    manager, svc = service
    svc._save_metadata = False
    stream_all(manager)
    session_dir = svc.start(RecordingMode.VIDEO, ["SIM-2"])
    assert wait_until(lambda: svc.status().frames_written >= 10)
    stats = svc.stop().cameras["SIM-2"]
    camera_dir = session_dir / "Camera_01"
    assert sorted(p.name for p in camera_dir.iterdir()) == ["video.mp4"]
    assert not (session_dir / "session.json").exists()
    assert stats.bytes_written == (camera_dir / "video.mp4").stat().st_size


def test_raw_without_metadata_keeps_its_index(service):
    manager, svc = service
    svc._save_metadata = False
    stream_all(manager)
    session_dir = svc.start(RecordingMode.RAW, ["SIM-1"])
    assert wait_until(lambda: svc.status().frames_written >= 5)
    svc.stop()
    camera_dir = session_dir / "Camera_01"
    assert sorted(p.name for p in camera_dir.iterdir()) == ["frames.csv", "frames.raw"]  # csv is the index
    assert not (session_dir / "session.json").exists()
    assert len(list(read_raw_sequence(camera_dir))) >= 5


def test_metadata_setting_comes_from_config(service):
    _, svc = service
    assert svc._save_metadata is True
    svc.reconfigure({**DEFAULT_CONFIG, "snapshots": {"directory": "snap"}})
    assert svc._save_metadata is False  # off by default


def test_refuses_to_start_when_disk_low(service, monkeypatch):
    manager, svc = service
    stream_all(manager)
    monkeypatch.setattr(svc, "_min_free_bytes", 10**18)
    with pytest.raises(RecordingError, match="disk space"):
        svc.start()


def test_stops_when_disk_runs_low(service, monkeypatch):
    manager, svc = service
    stream_all(manager)
    svc.start()
    monkeypatch.setattr(svc, "_min_free_bytes", 10**18)
    monkeypatch.setattr(svc, "_last_disk_check", 0.0)
    status = svc.status()
    assert not status.active and "disk space" in status.error


def test_snapshot_all_streaming(service, tmp_path):
    manager, svc = service
    stream_all(manager)
    files = svc.snapshot()
    assert len(files) == 2
    meta = json.loads(files[0].metadata.read_text())
    assert meta["camera"]["camera_id"] == "SIM-1" and meta["application"]["version"]
    assert files[0].raw.exists() and files[0].processed.exists()


# --- independent per-camera recording / capture ---------------------------------
def test_cameras_record_independently(service):
    manager, svc = service
    stream_all(manager)
    dir_a = svc.start(RecordingMode.RAW, ["SIM-1"])
    assert svc.is_recording("SIM-1") and not svc.is_recording("SIM-2")
    with pytest.raises(RecordingError, match="Already recording"):
        svc.start(RecordingMode.RAW, ["SIM-1"])
    dir_b = svc.start(RecordingMode.VIDEO, ["SIM-2"])
    assert dir_b != dir_a and svc.is_recording("SIM-2")
    assert svc.camera_recording("SIM-2").mode is RecordingMode.VIDEO
    assert wait_until(lambda: svc.camera_recording("SIM-1").stats.frames_written >= 5)

    stopped = svc.stop(["SIM-1"])  # stopping A must not touch B
    assert list(stopped.cameras) == ["SIM-1"] and stopped.cameras["SIM-1"].frames_written >= 5
    assert not svc.is_recording("SIM-1") and svc.is_recording("SIM-2") and svc.active
    assert json.loads((dir_a / "session.json").read_text())["session"]["stopped"]
    assert json.loads((dir_b / "session.json").read_text())["session"]["stopped"] is None
    svc.stop()
    assert not svc.active


def test_stop_one_camera_of_a_shared_session(service):
    manager, svc = service
    stream_all(manager)
    session_dir = svc.start(RecordingMode.RAW)  # toolbar: all streaming cameras
    assert svc.is_recording("SIM-1") and svc.is_recording("SIM-2")
    svc.stop(["SIM-2"])
    doc = json.loads((session_dir / "session.json").read_text())
    assert doc["session"]["stopped"] is None  # SIM-1 still recording
    assert [c["recording"]["stopped"] for c in doc["cameras"]] == [False, True]
    svc.stop(["SIM-1"])
    assert json.loads((session_dir / "session.json").read_text())["session"]["stopped"]


def test_toolbar_start_skips_cameras_already_recording(service):
    manager, svc = service
    stream_all(manager)
    svc.start(RecordingMode.RAW, ["SIM-1"])
    svc.start()  # records the rest
    assert svc.is_recording("SIM-2")
    with pytest.raises(RecordingError, match="No streaming cameras"):
        svc.start()


def test_cannot_record_a_stopped_camera(service):
    manager, svc = service
    manager.connect("SIM-1")
    with pytest.raises(RecordingError, match="Start the camera"):
        svc.start(RecordingMode.RAW, ["SIM-1"])


@pytest.mark.parametrize("mode,extension", [(RecordingMode.AVI, "avi"), (RecordingMode.MOV, "mov"),
                                            (RecordingMode.MKV, "mkv")])
def test_video_containers(service, mode, extension):
    manager, svc = service
    stream_all(manager)
    session_dir = svc.start(mode, ["SIM-1"])
    assert wait_until(lambda: svc.status().frames_written >= 8)
    svc.stop()
    capture = cv2.VideoCapture(str(session_dir / "Camera_01" / f"video.{extension}"))
    assert capture.isOpened() and int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) >= 8
    capture.release()


@pytest.mark.parametrize("image_format,extension,raw_extension", [
    ("png", "png", "png"), ("jpeg", "jpg", "png"), ("bmp", "bmp", "png"), ("tiff", "tif", "tif")])
def test_snapshot_formats(tmp_path, image_format, extension, raw_extension):
    raw = np.array([[0, 4095], [1234, 65535]], np.uint16)
    files = save_snapshot(frame(1, raw, "Mono16"), tmp_path, {}, image_format)
    assert files.processed.suffix == f".{extension}" and cv2.imread(str(files.processed)) is not None
    assert files.raw.suffix == f".{raw_extension}"
    np.testing.assert_array_equal(cv2.imread(str(files.raw), cv2.IMREAD_UNCHANGED), raw)  # raw stays lossless


def test_snapshot_one_camera_only(service):
    manager, svc = service
    stream_all(manager)
    files = svc.snapshot(["SIM-2"], "jpeg")
    assert len(files) == 1 and files[0].processed.name.startswith("SIM-2") and files[0].processed.suffix == ".jpg"
    with pytest.raises(RecordingError, match="Unsupported image format"):
        svc.snapshot(["SIM-2"], "gif")
