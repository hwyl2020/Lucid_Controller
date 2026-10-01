"""Checks ArenaCamera's buffer handling against the real installed Arena SDK (no camera needed).

Buffers are built with BufferFactory.create, so pixel copying and the SDK's Bayer conversion are
exercised on genuine arena_api objects. Skipped when the SDK is not installed.
"""

import ctypes

import numpy as np
import pytest

from app.cameras import arena_sdk
from app.cameras.arena_camera import ArenaCamera, _copy_pixels
from app.cameras.camera_discovery import ArenaDeviceInfo

try:
    SDK = arena_sdk.load()
except arena_sdk.ArenaSdkUnavailable:
    SDK = None

pytestmark = pytest.mark.skipif(SDK is None, reason="Arena SDK not installed")


@pytest.fixture
def make_buffer():
    created = []

    def make(data: np.ndarray, pixel_format: str):
        memory = np.ascontiguousarray(data).view(np.uint8).ravel()
        pdata = memory.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))
        height, width = data.shape[:2]
        buffer = SDK.buffer_factory.create(pdata, memory.size, width, height, pixel_format)
        created.append((buffer, memory))  # keep source memory alive while the buffer exists
        return buffer

    yield make
    for buffer, _ in created:
        SDK.buffer_factory.destroy(buffer)


def test_real_buffer_metadata(make_buffer):
    buffer = make_buffer(np.zeros((4, 6), np.uint8), "Mono8")
    assert (buffer.width, buffer.height, buffer.bits_per_pixel) == (6, 4, 8)
    assert buffer.pixel_format.name == "Mono8"
    assert isinstance(buffer.pdata, ctypes.POINTER(ctypes.c_uint8))


def test_copy_mono8(make_buffer):
    data = np.arange(24, dtype=np.uint8).reshape(4, 6)
    np.testing.assert_array_equal(_copy_pixels(make_buffer(data, "Mono8"), np.uint8, 1), data)


def test_copy_mono16(make_buffer):
    data = np.array([[0, 1, 65535], [256, 4095, 2]], dtype=np.uint16)
    buffer = make_buffer(data, "Mono16")
    assert buffer.bits_per_pixel == 16
    np.testing.assert_array_equal(_copy_pixels(buffer, np.uint16, 1), data)


def test_copy_rgb8(make_buffer):
    data = np.random.default_rng(0).integers(0, 256, (3, 5, 3), dtype=np.uint8)
    buffer = make_buffer(data, "RGB8")
    assert buffer.bits_per_pixel == 24
    np.testing.assert_array_equal(_copy_pixels(buffer, np.uint8, 3), data)


def test_bayer_rg8_conversion_puts_red_in_channel_0(make_buffer):
    # BayerRG: even rows R G R G, odd rows G B G B. Light only the red sites.
    data = np.zeros((8, 8), np.uint8)
    data[0::2, 0::2] = 200
    camera = ArenaCamera(ArenaDeviceInfo.from_sdk({"serial": "test", "mac": "x"}))
    rgb, pixel_format = camera._extract(make_buffer(data, "BayerRG8"))
    assert pixel_format == "RGB8" and rgb.shape == (8, 8, 3)
    inner = rgb[2:-2, 2:-2].reshape(-1, 3).mean(axis=0)  # avoid demosaic edge effects
    assert inner[0] > 3 * max(inner[1], inner[2]), f"expected red-dominant image, got mean RGB {inner}"
