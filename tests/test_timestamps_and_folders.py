"""Live frame timestamps, per-camera recording timers, and the Browse… folder fields."""

import copy
import re
import threading
import time

import dearpygui.dearpygui as dpg
import pytest

from app.cameras.camera_manager import CameraManager
from app.cameras.simulator_camera import SimulatorCamera, SimulatorConfig
from app.recording.recorder import RecorderStats
from app.services.app_services import AppServices
from app.services.configuration import DEFAULT_CONFIG
from app.services.recording_service import RecordingService, RecordingStatus
from app.ui.camera_view import CameraView
from app.ui.folder_picker import FolderPicker
from app.ui.settings_window import SettingsWindow
from app.ui.status_bar import format_duration, format_frame_time, format_recording_status


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


# --- formats ---------------------------------------------------------------------------
def test_format_duration():
    assert format_duration(0) == "00:00:00"
    assert format_duration(83.9) == "00:01:23"
    assert format_duration(3 * 3600 + 5) == "03:00:05"


def test_format_frame_time_has_milliseconds():
    ts = time.mktime((2026, 10, 6, 13, 45, 21, 0, 0, -1)) + 0.347
    assert format_frame_time(ts) == "13:45:21.347"
    assert format_frame_time(ts - 0.347 + 0.9996) == "13:45:22.000"  # rounds into the next second


def test_status_bar_lists_each_recording_camera_with_its_own_time():
    stats = RecorderStats(frames_written=120, bytes_written=2_000_000_000)
    status = RecordingStatus(active=True, elapsed_s=90, cameras={"A": stats, "B": stats}, free_bytes=100e9)
    text = format_recording_status(status, [("263401242", 90), ("263401243", 12)])
    assert text.startswith("● REC  263401242 00:01:30  ·  263401243 00:00:12 | 240 frames | 4.00 GB")


# --- tiles -----------------------------------------------------------------------------
@pytest.fixture
def tile(tmp_path):
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["recording"]["directory"] = str(tmp_path / "rec")
    manager = CameraManager(frame_timeout=0.1)
    manager.add_camera(SimulatorCamera(SimulatorConfig(camera_id="A", width=320, height=240, fps=30)))
    recording = RecordingService(manager, cfg)
    dpg.create_context()
    with dpg.window():
        with dpg.group() as row:
            pass
    view = CameraView(row, dpg.add_texture_registry())
    view.set_size(640, 400)
    view.assign("A", manager)
    yield view, manager, recording
    recording.stop()
    manager.shutdown()
    dpg.destroy_context()


def test_streaming_tile_shows_the_real_time_timestamp(tile):
    view, manager, recording = tile
    manager.connect("A")
    manager.start_streaming("A")
    # The tile polls the display queue itself (reading it here would consume the frame).
    assert wait_for(lambda: (view.update(manager, recording), view._frame_time)[1] != "")
    view.update(manager, recording)
    chips = [dpg.get_item_label(c) for c in (view._chip_time, view._chip_fps, view._chip_id)]
    assert re.fullmatch(r"\d\d:\d\d:\d\d\.\d{3}", chips[0]), chips
    assert re.fullmatch(r"[\d.]+ fps", chips[1]) and chips[2].startswith("#"), chips


def test_recording_tile_shows_its_own_recording_time(tile):
    view, manager, recording = tile
    manager.connect("A")
    manager.start_streaming("A")
    assert wait_for(lambda: manager.state("A").name == "ACQUIRING")
    recording.start(camera_ids=["A"])
    view.update(manager, recording)
    assert dpg.get_value(view._state) == "REC  00:00:00"


# --- Browse… --------------------------------------------------------------------------
def test_folder_picker_hands_the_choice_back_on_poll(tmp_path):
    chosen = []
    release = threading.Event()

    def ask(title, initial):
        release.wait(2)
        return str(tmp_path / "Recordings 2026")

    picker = FolderPicker(ask)
    picker.choose("Select", "", chosen.append)
    assert picker.is_open
    picker.choose("Select", "", chosen.append)  # only one dialog at a time
    picker.poll()
    assert chosen == []
    release.set()
    assert wait_for(lambda: (picker.poll(), chosen)[1] != [])
    assert chosen == [str(tmp_path / "Recordings 2026")] and not picker.is_open


def test_cancelled_dialog_changes_nothing():
    chosen = []
    picker = FolderPicker(lambda title, initial: "")
    picker.choose("Select", "", chosen.append)
    assert wait_for(lambda: (picker.poll(), not picker.is_open)[1])
    assert chosen == []


def test_settings_browse_fills_the_path_and_save_uses_it(tmp_path):
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    for key in ("recording", "snapshots", "profiles", "sessions", "diagnostics", "logging"):
        cfg[key]["directory"] = str(tmp_path / key)
    services = AppServices.create(cfg, tmp_path / "config.json", CameraManager())
    target = tmp_path / "chosen" / "recordings"
    dpg.create_context()
    try:
        picker = FolderPicker(lambda title, initial: str(target))
        window = SettingsWindow(services, on_theme=lambda name: None, picker=picker)
        window.show()
        window._browse(window._rec_dir, "Recordings folder")
        assert wait_for(lambda: (window.update(), dpg.get_value(window._rec_dir) == str(target))[1])
        window._save()
        assert services.config["recording"]["directory"] == str(target)
        assert services.recording._base_dir == target
    finally:
        dpg.destroy_context()


def test_settings_metadata_checkbox_round_trip(tmp_path):
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    for key in ("recording", "snapshots", "profiles", "sessions", "diagnostics", "logging"):
        cfg[key]["directory"] = str(tmp_path / key)
    services = AppServices.create(cfg, tmp_path / "config.json", CameraManager())
    dpg.create_context()
    try:
        window = SettingsWindow(services, on_theme=lambda name: None)
        window.show()
        assert dpg.get_value(window._metadata) is False  # off by default
        dpg.set_value(window._metadata, True)
        window._save()
        assert services.config["recording"]["save_metadata"] is True
        assert services.recording._save_metadata is True
    finally:
        dpg.destroy_context()


# --- timestamp burned into video recordings ---------------------------------------------
import cv2  # noqa: E402
import numpy as np  # noqa: E402

from app.acquisition.frame import Frame  # noqa: E402
from app.recording.recorder import RecordingMode  # noqa: E402
from app.recording.video_writer import VideoFileWriter, draw_timestamp, stamp_text  # noqa: E402


def test_stamp_text_has_date_time_and_milliseconds():
    ts = time.mktime((2026, 10, 6, 15, 32, 4, 0, 0, -1)) + 0.047
    assert stamp_text(ts) == "2026-10-06 15:32:04.047"


def test_draw_timestamp_marks_only_the_top_left_corner():
    rgb = np.full((760, 1000, 3), 128, np.uint8)
    draw_timestamp(rgb, time.time())
    assert (rgb[:60, :500] != 128).any()  # box and text
    assert (rgb[200:, :] == 128).all() and (rgb[:, 700:] == 128).all()


def _rgb_frame(i):
    data = np.full((240, 320, 3), 100, np.uint8)
    return Frame(camera_id="A", frame_id=i, timestamp=time.time(), width=320, height=240,
                 pixel_format="RGB8", data=data)


@pytest.mark.parametrize("stamp", [True, False])
def test_video_writer_burns_the_timestamp_only_when_asked(tmp_path, stamp):
    writer = VideoFileWriter(tmp_path, fps=10, container="avi", index=False, stamp=stamp)
    frames = [_rgb_frame(i) for i in range(3)]
    for frame in frames:
        writer.write(frame)
    writer.close()
    assert all((f.data == 100).all() for f in frames)  # the camera frames are never drawn on
    capture = cv2.VideoCapture(str(tmp_path / "video.avi"))
    ok, image = capture.read()
    capture.release()
    assert ok
    corner = image[:20, :80].astype(int)
    assert bool(abs(corner - 100).max() > 60) is stamp  # dark box + white text only when stamped


def test_camera_row_timestamp_switch(tmp_path):
    from app.services.network_service import NetworkService
    from app.ui.camera_sidebar import CameraSidebar
    from app.services.camera_status_service import CameraStatusService

    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["recording"]["directory"] = str(tmp_path / "rec")
    manager = CameraManager(frame_timeout=0.1)
    manager.add_camera(SimulatorCamera(SimulatorConfig(camera_id="A", width=160, height=120, fps=30)))
    recording = RecordingService(manager, cfg)
    dpg.create_context()
    try:
        with dpg.window():
            with dpg.group() as parent:
                pass
        row = CameraSidebar(parent, manager, CameraStatusService(manager), recording, NetworkService(manager),
                            on_property_grid=lambda cid: None).rows["A"]
        assert row.timestamp_on is True  # on by default
        dpg.set_value(row.video_format, RecordingMode.VIDEO.label)
        row.update()
        assert dpg.get_item_label(row.stamp_button) == "ON"
        row.toggle_timestamp()
        row.update()
        assert dpg.get_item_label(row.stamp_button) == "OFF" and row.timestamp_on is False
        dpg.set_value(row.video_format, RecordingMode.RAW.label)
        row.update()
        assert not dpg.get_item_configuration(row.stamp_button)["enabled"]  # raw is never altered
        assert dpg.get_value(row.stamp_label).startswith("Timestamp: in frames.csv")
    finally:
        recording.stop()
        manager.shutdown()
        dpg.destroy_context()
