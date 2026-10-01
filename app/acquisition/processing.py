"""Frame -> display conversion. Pure functions, no GUI or SDK imports."""

from __future__ import annotations

import cv2
import numpy as np

from app.acquisition.frame import Frame

# Unpacked mono formats (one uint16 per pixel) and their bit depth.
_MONO16_BITS = {"Mono10": 10, "Mono12": 12, "Mono16": 16}

# Bayer phase per GenICam PFNC: the name gives the 2x2 cell row by row, e.g. BayerRG = R G / G B.
# Positions (row, col) of R and B inside the cell; the other two sites are green.
_BAYER_PHASES = {"RG": ((0, 0), (1, 1)), "GR": ((0, 1), (1, 0)), "GB": ((1, 0), (0, 1)), "BG": ((1, 1), (0, 0))}
_BAYER_BITS = {"8": 8, "10": 10, "12": 12, "16": 16}

DISPLAY_PIXEL_FORMATS = (
    "Mono8", "RGB8", *_MONO16_BITS,
    *(f"Bayer{p}{b}" for p in _BAYER_PHASES for b in _BAYER_BITS),
)


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
    elif pixel_format.startswith("Bayer") and pixel_format[5:7] in _BAYER_PHASES and pixel_format[7:] in _BAYER_BITS:
        img, to_rgba = bayer_preview(data, pixel_format[5:7], _BAYER_BITS[pixel_format[7:]]), cv2.COLOR_RGB2RGBA
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


def bayer_preview(raw: np.ndarray, phase: str, bits: int = 8) -> np.ndarray:
    """Half-resolution RGB8 from raw Bayer by 2x2 binning (R, mean of the two Gs, B per cell).

    Display-only: far cheaper than full demosaicing and the multiview shows downscaled images
    anyway. Checked against the Arena SDK's own BayerRG8 -> RGB8 conversion on a TRI122S-C:
    per-channel correlation >= 0.999.
    """
    (ry, rx), (by, bx) = _BAYER_PHASES[phase]
    height, width = (raw.shape[0] // 2) * 2, (raw.shape[1] // 2) * 2
    cell = raw[:height, :width]
    green_sites = [(y, x) for y in (0, 1) for x in (0, 1) if (y, x) not in ((ry, rx), (by, bx))]

    def plane(y: int, x: int) -> np.ndarray:
        p = np.ascontiguousarray(cell[y::2, x::2])
        if bits > 8:
            p = (p >> (bits - 8)).astype(np.uint8)
        return p

    red, blue = plane(ry, rx), plane(by, bx)
    green = cv2.addWeighted(plane(*green_sites[0]), 0.5, plane(*green_sites[1]), 0.5, 0)
    return cv2.merge([red, green, blue])
