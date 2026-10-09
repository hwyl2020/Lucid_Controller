"""Image capture.

* **Raw** saves everything: the lossless camera data (``_raw.png``: native pixels such as raw Bayer,
  16-bit kept as 16-bit), a viewable full-resolution RGB PNG, and a JSON sidecar with the camera
  settings and frame details.
* **PNG / JPEG / BMP / TIFF** save only the viewable full-resolution RGB image in that format.
  (If a frame cannot be converted to RGB, its lossless raw copy is saved instead, so a capture
  never silently produces nothing.)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from app.acquisition.frame import Frame
from app.acquisition.processing import PixelConversionError, to_rgb8

# format id -> (extension, label)
IMAGE_FORMATS = {
    "raw": ("png", "Raw (lossless)"),  # raw data + PNG + JSON
    "png": ("png", "PNG"),
    "jpeg": ("jpg", "JPEG"),
    "bmp": ("bmp", "BMP"),
    "tiff": ("tif", "TIFF"),
}
JPEG_QUALITY = 95


@dataclass(frozen=True)
class SnapshotFiles:
    raw: Path | None  # lossless native data (Raw only); 16-bit kept as 16-bit
    processed: Path | None  # full-resolution 8-bit RGB in the chosen format (None if no conversion)
    metadata: Path | None  # JSON sidecar (Raw only)


def save_snapshot(frame: Frame, directory: Path, metadata: dict, image_format: str = "png",
                  name: str | None = None) -> SnapshotFiles:
    """``name``: file-name prefix (default: the camera id), e.g. "TRI122S-C_262503318"."""
    extension = IMAGE_FORMATS[image_format][0]
    everything = image_format == "raw"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(frame.timestamp))
    millis = int((frame.timestamp % 1) * 1000)
    stem = f"{_safe(name or frame.camera_id)}_{stamp}_{millis:03d}_f{frame.frame_id}"

    processed_path: Path | None = directory / f"{stem}.{extension}"
    params = [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY] if image_format == "jpeg" else []
    try:
        _imwrite(processed_path, cv2.cvtColor(to_rgb8(frame), cv2.COLOR_RGB2BGR), params)
    except PixelConversionError:
        processed_path = None

    raw_path: Path | None = None
    if everything or processed_path is None:
        raw_path = directory / f"{stem}_raw.png"
        raw = frame.data
        if raw.ndim == 3:  # RGB8 -> PNG wants BGR order
            raw = cv2.cvtColor(raw, cv2.COLOR_RGB2BGR)
        _imwrite(raw_path, np.ascontiguousarray(raw))
    if not everything:
        return SnapshotFiles(raw_path, processed_path, None)

    meta_path = directory / f"{stem}.json"
    sidecar = {
        **metadata,
        "frame": {
            "frame_id": frame.frame_id,
            "timestamp": frame.timestamp,
            "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(frame.timestamp)) + f".{millis:03d}",
            "width": frame.width,
            "height": frame.height,
            "pixel_format": frame.pixel_format,
            "dtype": frame.data.dtype.str,
        },
        "files": {"raw": raw_path.name if raw_path else None,
                  "processed": processed_path.name if processed_path else None,
                  "format": IMAGE_FORMATS[image_format][1]},
    }
    meta_path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    return SnapshotFiles(raw_path, processed_path, meta_path)


def _imwrite(path: Path, image: np.ndarray, params: list[int] | None = None) -> None:
    if not cv2.imwrite(str(path), image, params or []):
        raise OSError(f"Could not write {path}")


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
