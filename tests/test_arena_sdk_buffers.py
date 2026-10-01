"""Checks ArenaCamera's buffer handling against the real installed Arena SDK (no camera needed).

Buffers are built with BufferFactory.create, so pixel copying and the SDK's Bayer conversion are
exercised on genuine arena_api objects. Skipped when the SDK is not installed.
"""

import ctypes

import cv2
import numpy as np
import pytest

from app.acquisition.processing import bayer_preview
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


def test_bayer_rg8_stays_raw(make_buffer):
    data = np.arange(64, dtype=np.uint8).reshape(8, 8)
    camera = ArenaCamera(ArenaDeviceInfo.from_sdk({"serial": "test", "mac": "x"}))
    raw, pixel_format = camera._extract(make_buffer(data, "BayerRG8"))
    assert pixel_format == "BayerRG8"
    np.testing.assert_array_equal(raw, data)


def test_bayer_preview_matches_sdk_conversion(make_buffer):
    """Our display preview must agree with the SDK's own BayerRG8 -> RGB8 demosaic."""
    rng = np.random.default_rng(1)
    # Smooth, colourful scene so demosaic edge effects are small.
    yy, xx = np.mgrid[0:64, 0:64] / 64.0
    scene = np.stack([200 * xx, 150 * yy, 100 * (1 - xx)], axis=-1) + rng.normal(0, 1, (64, 64, 3))
    raw = np.zeros((64, 64), np.uint8)
    raw[0::2, 0::2] = scene[0::2, 0::2, 0]
    raw[0::2, 1::2] = scene[0::2, 1::2, 1]
    raw[1::2, 0::2] = scene[1::2, 0::2, 1]
    raw[1::2, 1::2] = scene[1::2, 1::2, 2]

    sdk_rgb_buffer = SDK.buffer_factory.convert(make_buffer(raw, "BayerRG8"), SDK.enums.PixelFormat.RGB8)
    try:
        sdk_rgb = _copy_pixels(sdk_rgb_buffer, np.uint8, 3)
    finally:
        SDK.buffer_factory.destroy(sdk_rgb_buffer)
    sdk_half = cv2.resize(sdk_rgb, (32, 32), interpolation=cv2.INTER_AREA).astype(float)
    preview = bayer_preview(raw, "RG").astype(float)

    inner = (slice(2, -2), slice(2, -2))
    for channel in range(3):
        diff = np.abs(sdk_half[inner][..., channel] - preview[inner][..., channel]).mean()
        assert diff < 6, f"channel {channel} differs from SDK demosaic by {diff:.1f} on average"
