"""Processed-video recording (secondary mode): half-resolution MP4 for quick review.

Measured on the dev PC: mp4v encodes a full 12 MP frame in ~92 ms (max ~11 FPS, too close to a
9 FPS camera) but a half-resolution frame in ~22 ms. Bayer frames are therefore 2x2-binned
(bayer_preview); other formats are downscaled to the same half size. Use raw mode for full quality.
"""

from __future__ import annotations

import csv
import os
import time
from pathlib import Path

import numpy as np

import cv2

from app.acquisition.frame import Frame
from app.acquisition.processing import to_rgb8

# Container -> (extension, FourCC). Each was verified on the dev PC to write and read back with the
# bundled OpenCV/FFmpeg, and to encode a 2012x1518 frame in 16-22 ms (45-64 FPS).
CONTAINERS = {
    "mp4": ("mp4", "mp4v"),
    "avi": ("avi", "MJPG"),
    "mov": ("mov", "mp4v"),
    "mkv": ("mkv", "XVID"),
}
INDEX_FILE = "frames.csv"


def stamp_text(timestamp: float) -> str:
    """Burned-in timestamp: local date and time with milliseconds, e.g. 2026-10-06 15:32:04.047."""
    millis = int(round((timestamp % 1) * 1000))
    if millis == 1000:
        timestamp, millis = timestamp + 1, 0
    return f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp))}.{millis:03d}"


def draw_timestamp(rgb: np.ndarray, timestamp: float) -> None:
    """Burn the frame's timestamp into the top-left corner (white on a dark box), in place.
    Sized to the frame (~3% of its height) so it stays readable at any resolution."""
    height, width = rgb.shape[:2]
    text = stamp_text(timestamp)
    scale = max(0.4, height / 760)
    thickness = max(1, round(scale * 1.6))
    (text_w, text_h), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
    pad = max(4, int(text_h * 0.45))
    x0, y0 = pad, pad
    x1, y1 = min(width - 1, x0 + text_w + 2 * pad), min(height - 1, y0 + text_h + baseline + 2 * pad)
    box = rgb[y0:y1, x0:x1]
    box[:] = (box * 0.35).astype(rgb.dtype)  # darken behind the text
    cv2.putText(rgb, text, (x0 + pad, y0 + pad + text_h), cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255),
                thickness, cv2.LINE_AA)


class VideoFileWriter:
    """``write`` returns how much the video file grew (compressed bytes on disk, not the size of the
    frame going in); ``total_bytes`` is the final file size once closed. ``index``: also write
    frames.csv (video frame -> camera frame id + timestamp). ``stamp``: burn each frame's real-time
    timestamp into the picture."""

    def __init__(self, directory: Path, fps: float, container: str = "mp4", index: bool = True,
                 stamp: bool = False) -> None:
        self._stamp = stamp
        directory.mkdir(parents=True, exist_ok=True)
        extension, self._fourcc = CONTAINERS[container]
        self._path = directory / f"video.{extension}"
        self._fps = max(float(fps), 1.0)
        self._writer: cv2.VideoWriter | None = None
        self._size: tuple[int, int] | None = None
        self._index_file = open(directory / INDEX_FILE, "w", newline="", encoding="utf-8") if index else None
        self._index = csv.writer(self._index_file) if self._index_file else None
        if self._index:
            self._index.writerow(("video_frame", "frame_id", "timestamp"))
        self._count = 0
        self._reported = 0  # file size already reported through write()

    def write(self, frame: Frame) -> int:
        rgb = to_rgb8(frame, full_resolution=False)
        if not frame.pixel_format.startswith("Bayer"):
            rgb = cv2.resize(rgb, (max(2, frame.width // 2), max(2, frame.height // 2)), interpolation=cv2.INTER_AREA)
        height, width = rgb.shape[:2]
        if self._stamp:
            if np.shares_memory(rgb, frame.data) or not rgb.flags.writeable:
                rgb = rgb.copy()  # never draw into the camera frame (display/snapshots share it)
            draw_timestamp(rgb, frame.timestamp)
        if self._writer is None:
            self._size = (width, height)
            self._writer = cv2.VideoWriter(str(self._path), cv2.VideoWriter_fourcc(*self._fourcc), self._fps, self._size)
            if not self._writer.isOpened():
                raise OSError(f"Could not open video writer for {self._path}")
        elif (width, height) != self._size:
            raise ValueError(
                f"Frame size changed from {self._size[0]}x{self._size[1]} to {width}x{height}; "
                "video recording cannot continue (use raw mode to record ROI changes)"
            )
        self._writer.write(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        if self._index:
            self._index.writerow((self._count, frame.frame_id, f"{frame.timestamp:.6f}"))
        self._count += 1
        return self._grown()

    def _grown(self) -> int:
        """Bytes the encoder has written to the file since the last call (it writes in chunks)."""
        try:
            size = os.path.getsize(self._path)
        except OSError:
            return 0
        grown, self._reported = max(0, size - self._reported), max(size, self._reported)
        return grown

    @property
    def total_bytes(self) -> int:
        """Size of the video file on disk (final once closed)."""
        try:
            return os.path.getsize(self._path)
        except OSError:
            return self._reported

    @property
    def path(self) -> Path:
        return self._path

    def close(self) -> None:
        if self._writer is not None:
            self._writer.release()
        if self._index_file:
            self._index_file.close()
