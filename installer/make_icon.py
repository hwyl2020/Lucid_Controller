"""Generate app/resources/app.ico (the .exe and window icon) with NumPy + OpenCV only.

A rounded azure square with a camera lens. The .ico holds PNG-compressed images at the standard
Windows sizes. Run: python -m installer.make_icon
"""

from __future__ import annotations

import struct
from pathlib import Path

import cv2
import numpy as np

SIZES = (16, 24, 32, 48, 64, 128, 256)
OUT = Path(__file__).resolve().parents[1] / "app" / "resources" / "app.ico"
SS = 4  # supersampling for smooth edges


def _icon(size: int) -> np.ndarray:
    n = size * SS
    y, x = np.mgrid[0:n, 0:n].astype(np.float32) + 0.5
    img = np.zeros((n, n, 4), np.float32)  # BGRA, 0..1

    def paint(mask: np.ndarray, bgr: tuple[float, float, float]) -> None:
        a = np.clip(mask, 0, 1)[..., None]
        img[..., :3] = img[..., :3] * (1 - a) + np.array(bgr, np.float32) * a
        img[..., 3:] = np.maximum(img[..., 3:], a)

    # Rounded square with a vertical azure gradient.
    r, pad = 0.22 * n, 0.04 * n
    qx = np.clip(x, pad + r, n - pad - r)
    qy = np.clip(y, pad + r, n - pad - r)
    square = (r - np.hypot(x - qx, y - qy)) / SS + 0.5
    top, bottom = np.array((255, 160, 40), np.float32) / 255, np.array((200, 90, 10), np.float32) / 255
    t = (y / n)[..., None]
    gradient = top * (1 - t) + bottom * t
    a = np.clip(square, 0, 1)[..., None]
    img[..., :3] = gradient * a
    img[..., 3:] = a

    c = n / 2
    d = np.hypot(x - c, y - c)
    paint((0.34 * n - d) / SS + 0.5, (0.98, 0.98, 0.98))  # lens ring
    paint((0.25 * n - d) / SS + 0.5, (0.32, 0.17, 0.08))  # lens body
    paint((0.13 * n - d) / SS + 0.5, (0.85, 0.55, 0.20))  # glass
    hx, hy = c - 0.06 * n, c - 0.06 * n
    paint((0.045 * n - np.hypot(x - hx, y - hy)) / SS + 0.5, (1.0, 1.0, 1.0))  # highlight

    small = cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)
    return (np.clip(small, 0, 1) * 255).round().astype(np.uint8)


def write_ico(path: Path) -> None:
    images = []
    for size in SIZES:
        ok, png = cv2.imencode(".png", _icon(size))
        assert ok
        images.append((size, png.tobytes()))
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, data in images:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset + len(blobs))
        blobs += data
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + entries + blobs)


if __name__ == "__main__":
    write_ico(OUT)
    print(f"wrote {OUT}")
