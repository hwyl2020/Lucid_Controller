"""Application-level frame. Backends copy SDK buffers into this; SDK objects never leave the backend."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class Frame:
    camera_id: str
    frame_id: int
    timestamp: float  # host capture time, seconds since epoch (time.time())
    width: int
    height: int
    pixel_format: str  # GenICam name, e.g. "Mono8", "RGB8"
    data: np.ndarray  # (height, width) for mono, (height, width, 3) for RGB; owned by the frame
