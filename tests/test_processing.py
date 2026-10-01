import numpy as np
import pytest

from app.acquisition.frame import Frame
from app.acquisition.processing import PixelConversionError, to_display_rgba


def make_frame(data, pixel_format):
    h, w = data.shape[:2]
    return Frame("CAM", 1, 0.0, w, h, pixel_format, data)


def test_mono8_to_rgba():
    data = np.array([[0, 255]], dtype=np.uint8)
    out = to_display_rgba(make_frame(data, "Mono8"))
    assert out.shape == (1, 2, 4) and out.dtype == np.float32
    np.testing.assert_allclose(out[0, 0], [0, 0, 0, 1])
    np.testing.assert_allclose(out[0, 1], [1, 1, 1, 1])


def test_rgb8_keeps_channel_order():
    data = np.zeros((1, 1, 3), dtype=np.uint8)
    data[0, 0] = (255, 0, 0)
    out = to_display_rgba(make_frame(data, "RGB8"))
    np.testing.assert_allclose(out[0, 0], [1, 0, 0, 1])


@pytest.mark.parametrize("fmt,max_value", [("Mono10", 1023), ("Mono12", 4095), ("Mono16", 65535)])
def test_high_bit_depth_mono_scaled_to_full_range(fmt, max_value):
    data = np.array([[0, max_value]], dtype=np.uint16)
    out = to_display_rgba(make_frame(data, fmt))
    assert out[0, 0, 0] == 0
    assert out[0, 1, 0] == pytest.approx(1.0)


def test_large_frame_downscaled_keeping_aspect():
    data = np.zeros((1000, 2000), dtype=np.uint8)
    out = to_display_rgba(make_frame(data, "Mono8"), max_side=500)
    assert out.shape == (250, 500, 4)


def test_small_frame_not_upscaled():
    data = np.zeros((100, 200), dtype=np.uint8)
    assert to_display_rgba(make_frame(data, "Mono8"), max_side=500).shape == (100, 200, 4)


def test_reuses_matching_output_buffer():
    frame = make_frame(np.full((10, 20), 255, np.uint8), "Mono8")
    out = np.zeros((10, 20, 4), np.float32)
    result = to_display_rgba(frame, out=out)
    assert result is out
    assert out.min() == pytest.approx(1.0)


def test_mismatched_output_buffer_is_replaced():
    frame = make_frame(np.zeros((10, 20), np.uint8), "Mono8")
    out = np.zeros((5, 5, 4), np.float32)
    assert to_display_rgba(frame, out=out).shape == (10, 20, 4)


def test_unsupported_format_raises():
    with pytest.raises(PixelConversionError):
        to_display_rgba(make_frame(np.zeros((2, 2), np.uint8), "BayerRG8"))
