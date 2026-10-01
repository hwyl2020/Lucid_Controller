"""Synthetic camera for UI development and tests without hardware or the Arena SDK."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import cv2
import numpy as np

from app.acquisition.frame import Frame
from app.cameras.camera_device import (
    CameraDevice,
    CameraDisconnectedError,
    CameraNotConnectedError,
    FrameTimeoutError,
    InvalidStateError,
    InvalidValueError,
    NumericRange,
    Roi,
    RoiLimits,
)

PATTERNS = ("moving_bar", "gradient", "checkerboard", "noise")
PIXEL_FORMATS = ("Mono8", "RGB8")


@dataclass
class SimulatorConfig:
    camera_id: str = "SIM-01"
    model: str = "Simulator"
    serial_number: str = "SIM00000001"
    ip_address: str | None = None
    width: int = 1280
    height: int = 720
    fps: float = 30.0
    pattern: str = "moving_bar"
    pixel_format: str = "Mono8"


class SimulatorCamera(CameraDevice):
    EXPOSURE_RANGE = NumericRange(20.0, 1_000_000.0)
    GAIN_RANGE = NumericRange(0.0, 24.0)
    FRAME_RATE_RANGE = NumericRange(1.0, 240.0)
    REFERENCE_EXPOSURE_US = 10_000.0  # exposure at which the pattern is rendered unscaled

    def __init__(self, config: SimulatorConfig | None = None) -> None:
        self._config = config = config or SimulatorConfig()
        if config.pattern not in PATTERNS:
            raise ValueError(f"Unknown pattern {config.pattern!r}; expected one of {PATTERNS}")
        if config.pixel_format not in PIXEL_FORMATS:
            raise ValueError(f"Unsupported pixel format {config.pixel_format!r}")
        self.FRAME_RATE_RANGE.validate(config.fps, "fps")

        self._roi_limits = RoiLimits(
            sensor_width=config.width,
            sensor_height=config.height,
            min_width=64,
            min_height=64,
            width_increment=8,
            height_increment=2,
            offset_x_increment=8,
            offset_y_increment=2,
        )
        self._lock = threading.Lock()
        self._connected = False
        self._acquiring = False
        self._lost = False
        self._exposure = self.REFERENCE_EXPOSURE_US
        self._gain = 0.0
        self._fps = config.fps
        self._pixel_format = config.pixel_format
        self._roi = self._roi_limits.full_frame()
        self._frame_id = 0
        self._next_due = 0.0
        self._rng = np.random.default_rng()
        self._base = self._make_base_pattern()

    # --- identity / state -------------------------------------------------
    @property
    def camera_id(self) -> str:
        return self._config.camera_id

    @property
    def model(self) -> str:
        return self._config.model

    @property
    def serial_number(self) -> str:
        return self._config.serial_number

    @property
    def ip_address(self) -> str | None:
        return self._config.ip_address

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def acquiring(self) -> bool:
        return self._acquiring

    # --- lifecycle --------------------------------------------------------
    def connect(self) -> None:
        with self._lock:
            self._lost = False
            self._connected = True

    def disconnect(self) -> None:
        with self._lock:
            self._acquiring = False
            self._connected = False

    def start_acquisition(self) -> None:
        with self._lock:
            self._require_connected()
            self._acquiring = True
            self._frame_id = 0
            self._next_due = time.perf_counter()

    def stop_acquisition(self) -> None:
        with self._lock:
            self._acquiring = False

    def simulate_disconnect(self) -> None:
        """Make the device disappear, as if the cable was pulled. connect() restores it."""
        with self._lock:
            self._lost = True
            self._acquiring = False
            self._connected = False

    def get_frame(self, timeout: float = 1.0) -> Frame:
        with self._lock:
            self._require_streaming()
            due = self._next_due

        wait = due - time.perf_counter()
        if wait > timeout:
            time.sleep(max(timeout, 0.0))
            raise FrameTimeoutError(f"{self.camera_id}: no frame within {timeout:.3f}s")
        if wait > 0:
            time.sleep(wait)

        with self._lock:
            self._require_streaming()  # state may have changed while sleeping
            self._frame_id += 1
            frame_id = self._frame_id
            period = 1.0 / self._fps
            self._next_due = due + period
            now = time.perf_counter()
            if self._next_due < now:
                # Consumer fell behind: like a real camera, skip ahead instead of bursting.
                self._next_due = now + period
            roi, pixel_format = self._roi, self._pixel_format
            scale = (self._exposure / self.REFERENCE_EXPOSURE_US) * 10 ** (self._gain / 20)

        data = self._render(frame_id, roi, pixel_format, scale)
        return Frame(
            camera_id=self.camera_id,
            frame_id=frame_id,
            timestamp=time.time(),
            width=roi.width,
            height=roi.height,
            pixel_format=pixel_format,
            data=data,
        )

    # --- capabilities -----------------------------------------------------
    def exposure_range(self) -> NumericRange | None:
        return self.EXPOSURE_RANGE

    def gain_range(self) -> NumericRange | None:
        return self.GAIN_RANGE

    def frame_rate_range(self) -> NumericRange | None:
        return self.FRAME_RATE_RANGE

    def pixel_formats(self) -> list[str]:
        return list(PIXEL_FORMATS)

    def roi_limits(self) -> RoiLimits | None:
        return self._roi_limits

    # --- current values ---------------------------------------------------
    @property
    def exposure(self) -> float | None:
        return self._exposure

    @property
    def gain(self) -> float | None:
        return self._gain

    @property
    def frame_rate(self) -> float | None:
        return self._fps

    @property
    def pixel_format(self) -> str:
        return self._pixel_format

    @property
    def roi(self) -> Roi:
        return self._roi

    # --- setters ----------------------------------------------------------
    def set_exposure(self, value: float) -> None:
        self.EXPOSURE_RANGE.validate(value, "exposure")
        with self._lock:
            self._require_connected()
            self._exposure = float(value)

    def set_gain(self, value: float) -> None:
        self.GAIN_RANGE.validate(value, "gain")
        with self._lock:
            self._require_connected()
            self._gain = float(value)

    def set_frame_rate(self, value: float) -> None:
        self.FRAME_RATE_RANGE.validate(value, "frame rate")
        with self._lock:
            self._require_connected()
            self._fps = float(value)

    def set_pixel_format(self, value: str) -> None:
        if value not in PIXEL_FORMATS:
            raise InvalidValueError(f"Pixel format {value!r} not supported; expected one of {PIXEL_FORMATS}")
        with self._lock:
            self._require_connected()
            self._require_not_acquiring("pixel format")
            self._pixel_format = value

    def set_roi(self, x: int, y: int, width: int, height: int) -> None:
        roi = Roi(x, y, width, height)
        self._roi_limits.validate(roi)
        with self._lock:
            self._require_connected()
            self._require_not_acquiring("ROI")
            self._roi = roi

    # --- internals --------------------------------------------------------
    def _require_connected(self) -> None:
        if self._lost:
            raise CameraDisconnectedError(f"{self.camera_id}: device lost")
        if not self._connected:
            raise CameraNotConnectedError(f"{self.camera_id}: not connected")

    def _require_streaming(self) -> None:
        self._require_connected()
        if not self._acquiring:
            raise InvalidStateError(f"{self.camera_id}: acquisition not started")

    def _require_not_acquiring(self, what: str) -> None:
        if self._acquiring:
            raise InvalidStateError(f"{self.camera_id}: stop acquisition before changing {what}")

    def _make_base_pattern(self) -> np.ndarray:
        h, w = self._config.height, self._config.width
        if self._config.pattern == "checkerboard":
            yy, xx = np.indices((h, w))
            return (((yy // 64) + (xx // 64)) % 2 * 200 + 30).astype(np.uint8)
        ramp = np.linspace(0, 255, w, dtype=np.float32)
        if self._config.pattern == "moving_bar":
            ramp *= 0.35  # dim background so the bar stands out
        return np.broadcast_to(ramp.astype(np.uint8), (h, w)).copy()

    def _render(self, frame_id: int, roi: Roi, pixel_format: str, scale: float) -> np.ndarray:
        h, w = self._config.height, self._config.width
        if self._config.pattern == "noise":
            img = self._rng.integers(0, 256, (h, w), dtype=np.uint8)
        else:
            img = self._base.copy()
        if self._config.pattern == "moving_bar":
            x = (frame_id * 8) % w
            img[:, x : x + 40] = 255

        img = np.ascontiguousarray(img[roi.y : roi.y + roi.height, roi.x : roi.x + roi.width])
        if not np.isclose(scale, 1.0):
            img = cv2.convertScaleAbs(img, alpha=scale)
        cv2.putText(
            img, f"{self.camera_id}  #{frame_id}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, 255, 2
        )
        if pixel_format == "RGB8":
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
        return img
