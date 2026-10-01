"""Frame -> display conversion. Pure functions, no GUI or SDK imports."""

from __future__ import annotations

import cv2
import numpy as np

from app.acquisition.frame import Frame

# Unpacked mono formats (one uint16 per pixel) and their bit depth.
_MONO16_BITS = {"Mono10": 10, "Mono12": 12, "Mono16": 16}

# Bayer formats are intentionally absent until verified against a real camera:
# GenICam and OpenCV name Bayer phases differently, so the mapping must be tested, not assumed.
DISPLAY_PIXEL_FORMATS = ("Mono8", "RGB8", *_MONO16_BITS)


class PixelConversionError(Exception):
    pass


def to_display_rgba(frame: Frame, max_side: int = 1024, out: np.ndarray | None = None) -> np.ndarray:
    """Convert a frame to a contiguous float32 (h, w, 4) RGBA array in [0, 1] for a GPU texture.

    Frames larger than ``max_side`` on their long edge are downscaled to bound conversion cost.
    If ``out`` has the resulting shape it is written in place; reusing it avoids a large
    allocation per frame, which dominates the cost otherwise.
    """
    data = frame.data
    pixel_format = frame.pixel_format

    if pixel_format == "Mono8":
        img, to_rgba = data, cv2.COLOR_GRAY2RGBA
    elif pixel_format in _MONO16_BITS:
        img = (data >> (_MONO16_BITS[pixel_format] - 8)).astype(np.uint8)
        to_rgba = cv2.COLOR_GRAY2RGBA
    elif pixel_format == "RGB8":
        img, to_rgba = data, cv2.COLOR_RGB2RGBA
    else:
        raise PixelConversionError(f"No display conversion for {pixel_format!r}")

    height, width = img.shape[:2]
    scale = max_side / max(height, width)
    if scale < 1.0:
        size = (max(1, round(width * scale)), max(1, round(height * scale)))
        img = cv2.resize(img, size, interpolation=cv2.INTER_AREA)

    rgba8 = cv2.cvtColor(img, to_rgba)
    if out is None or out.shape != rgba8.shape or out.dtype != np.float32:
        out = np.empty(rgba8.shape, np.float32)
    np.multiply(rgba8, np.float32(1.0 / 255.0), out=out, casting="unsafe")
    return out
