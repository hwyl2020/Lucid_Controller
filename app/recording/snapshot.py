"""Snapshot: raw frame + processed RGB image + JSON metadata sidecar."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from app.acquisition.frame import Frame
from app.acquisition.processing import PixelConversionError, to_rgb8


@dataclass(frozen=True)
class SnapshotFiles:
    raw: Path  # lossless native data: 8-bit -> PNG, 16-bit -> 16-bit PNG
    processed: Path | None  # full-resolution 8-bit RGB PNG (None if no conversion exists)
    metadata: Path


def save_snapshot(frame: Frame, directory: Path, metadata: dict) -> SnapshotFiles:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(frame.timestamp))
    millis = int((frame.timestamp % 1) * 1000)
    stem = f"{_safe(frame.camera_id)}_{stamp}_{millis:03d}_f{frame.frame_id}"

    raw_path = directory / f"{stem}_raw.png"
    raw = frame.data
    if raw.ndim == 3:  # RGB8 -> PNG wants BGR order
        raw = cv2.cvtColor(raw, cv2.COLOR_RGB2BGR)
    _imwrite(raw_path, np.ascontiguousarray(raw))

    processed_path: Path | None = directory / f"{stem}.png"
    try:
        _imwrite(processed_path, cv2.cvtColor(to_rgb8(frame), cv2.COLOR_RGB2BGR))
    except PixelConversionError:
        processed_path = None

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
        "files": {"raw": raw_path.name, "processed": processed_path.name if processed_path else None},
    }
    meta_path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    return SnapshotFiles(raw_path, processed_path, meta_path)


def _imwrite(path: Path, image: np.ndarray) -> None:
    if not cv2.imwrite(str(path), image):
        raise OSError(f"Could not write {path}")


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
