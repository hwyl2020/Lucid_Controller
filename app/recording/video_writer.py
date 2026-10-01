"""Processed-video recording (secondary mode): half-resolution MP4 for quick review.

Measured on the dev PC: mp4v encodes a full 12 MP frame in ~92 ms (max ~11 FPS, too close to a
9 FPS camera) but a half-resolution frame in ~22 ms. Bayer frames are therefore 2x2-binned
(bayer_preview); other formats are downscaled to the same half size. Use raw mode for full quality.
"""

from __future__ import annotations

import csv
from pathlib import Path

import cv2

from app.acquisition.frame import Frame
from app.acquisition.processing import to_rgb8

VIDEO_FILE = "video.mp4"
INDEX_FILE = "frames.csv"


class VideoFileWriter:
    def __init__(self, directory: Path, fps: float) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self._path = directory / VIDEO_FILE
        self._fps = max(float(fps), 1.0)
        self._writer: cv2.VideoWriter | None = None
        self._size: tuple[int, int] | None = None
        self._index_file = open(directory / INDEX_FILE, "w", newline="", encoding="utf-8")
        self._index = csv.writer(self._index_file)
        self._index.writerow(("video_frame", "frame_id", "timestamp"))
        self._count = 0

    def write(self, frame: Frame) -> int:
        rgb = to_rgb8(frame, full_resolution=False)
        if not frame.pixel_format.startswith("Bayer"):
            rgb = cv2.resize(rgb, (max(2, frame.width // 2), max(2, frame.height // 2)), interpolation=cv2.INTER_AREA)
        height, width = rgb.shape[:2]
        if self._writer is None:
            self._size = (width, height)
            self._writer = cv2.VideoWriter(str(self._path), cv2.VideoWriter_fourcc(*"mp4v"), self._fps, self._size)
            if not self._writer.isOpened():
                raise OSError(f"Could not open video writer for {self._path}")
        elif (width, height) != self._size:
            raise ValueError(
                f"Frame size changed from {self._size[0]}x{self._size[1]} to {width}x{height}; "
                "video recording cannot continue (use raw mode to record ROI changes)"
            )
        self._writer.write(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        self._index.writerow((self._count, frame.frame_id, f"{frame.timestamp:.6f}"))
        self._count += 1
        return rgb.nbytes

    def close(self) -> None:
        if self._writer is not None:
            self._writer.release()
        self._index_file.close()
