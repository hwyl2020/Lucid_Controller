import numpy as np
import pytest

from app.acquisition.frame import Frame
from app.acquisition.frame_queue import LatestFrameQueue, RecordingQueue


def make_frame(frame_id: int, camera_id: str = "CAM") -> Frame:
    return Frame(camera_id, frame_id, 0.0, 4, 4, "Mono8", np.zeros((4, 4), np.uint8))


class TestLatestFrameQueue:
    def test_empty_returns_none(self):
        assert LatestFrameQueue().get_latest() is None

    def test_returns_newest_and_discards_rest(self):
        q = LatestFrameQueue(maxsize=3)
        for i in range(3):
            q.put(make_frame(i))
        assert q.get_latest().frame_id == 2
        assert len(q) == 0
        assert q.dropped == 2

    def test_put_never_blocks_and_evicts_oldest(self):
        q = LatestFrameQueue(maxsize=2)
        for i in range(10):
            q.put(make_frame(i))
        assert len(q) == 2
        assert q.dropped == 8
        assert q.get_latest().frame_id == 9

    def test_invalid_size(self):
        with pytest.raises(ValueError):
            LatestFrameQueue(0)


class TestRecordingQueue:
    def test_fifo_order(self):
        q = RecordingQueue(maxsize=5)
        for i in range(3):
            assert q.put(make_frame(i))
        assert [q.get(timeout=0).frame_id for _ in range(3)] == [0, 1, 2]

    def test_overflow_is_reported_not_silent(self, caplog):
        q = RecordingQueue(maxsize=2)
        assert q.put(make_frame(0))
        assert q.put(make_frame(1))
        assert not q.put(make_frame(2))
        assert q.overflow_count == 1
        assert q.depth == 2
        assert "Recording queue full" in caplog.text

    def test_get_empty_times_out(self):
        assert RecordingQueue().get(timeout=0.01) is None
