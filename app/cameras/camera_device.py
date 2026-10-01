"""Hardware-independent camera interface shared by ArenaCamera and SimulatorCamera.

This is the contract between the camera engine and the UI/services. Change it deliberately.

Conventions:
- Exposure is in microseconds (GenICam ExposureTime), gain in dB, frame rate in Hz.
- Capability queries return None (or an empty list) when the camera does not support the feature;
  the UI hides/disables the control instead of failing.
- All expected failures raise a CameraError subclass. Callers must catch CameraError, never let it
  terminate the application.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.acquisition.frame import Frame


class CameraError(Exception):
    """Base for expected camera failures."""


class CameraNotConnectedError(CameraError):
    pass


class CameraDisconnectedError(CameraError):
    """The device was lost while in use."""


class FrameTimeoutError(CameraError):
    pass


class IncompleteFrameError(FrameTimeoutError):
    """A frame arrived with missing data and was discarded. Retry like a timeout."""


class UnsupportedFeatureError(CameraError):
    pass


class InvalidValueError(CameraError):
    pass


class InvalidStateError(CameraError):
    """Operation not allowed in the current state, e.g. changing pixel format while acquiring."""


@dataclass(frozen=True)
class NumericRange:
    minimum: float
    maximum: float
    increment: float | None = None  # None = continuous

    def validate(self, value: float, name: str = "value") -> None:
        if not self.minimum <= value <= self.maximum:
            raise InvalidValueError(f"{name} {value} outside [{self.minimum}, {self.maximum}]")
        if self.increment:
            steps = (value - self.minimum) / self.increment
            if not math.isclose(steps, round(steps), abs_tol=1e-6):
                raise InvalidValueError(
                    f"{name} {value} must be {self.minimum} + n * {self.increment}"
                )

    def clamp(self, value: float) -> float:
        """Nearest valid value: clamped to the range and snapped to the increment."""
        value = min(max(value, self.minimum), self.maximum)
        if self.increment:
            value = self.minimum + round((value - self.minimum) / self.increment) * self.increment
            if value > self.maximum:
                value -= self.increment
        return value


@dataclass(frozen=True)
class Roi:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class RoiLimits:
    sensor_width: int
    sensor_height: int
    min_width: int
    min_height: int
    width_increment: int = 1
    height_increment: int = 1
    offset_x_increment: int = 1
    offset_y_increment: int = 1

    def full_frame(self) -> Roi:
        return Roi(0, 0, self.sensor_width, self.sensor_height)

    def validate(self, roi: Roi) -> None:
        _check_axis("width", roi.width, self.min_width, self.sensor_width, self.width_increment)
        _check_axis("height", roi.height, self.min_height, self.sensor_height, self.height_increment)
        _check_axis("offset x", roi.x, 0, self.sensor_width - roi.width, self.offset_x_increment)
        _check_axis("offset y", roi.y, 0, self.sensor_height - roi.height, self.offset_y_increment)

    def clamp(self, roi: Roi) -> Roi:
        """Largest valid ROI not exceeding the request: sizes first, then offsets that still fit."""
        width = _snap_down(roi.width, self.min_width, self.sensor_width, self.width_increment)
        height = _snap_down(roi.height, self.min_height, self.sensor_height, self.height_increment)
        x = _snap_down(roi.x, 0, self.sensor_width - width, self.offset_x_increment)
        y = _snap_down(roi.y, 0, self.sensor_height - height, self.offset_y_increment)
        return Roi(x, y, width, height)


def _snap_down(value: int, minimum: int, maximum: int, increment: int) -> int:
    value = min(max(int(value), minimum), maximum)
    return minimum + (value - minimum) // increment * increment


def _check_axis(name: str, value: int, minimum: int, maximum: int, increment: int) -> None:
    if not minimum <= value <= maximum:
        raise InvalidValueError(f"ROI {name} {value} outside [{minimum}, {maximum}]")
    if (value - minimum) % increment:
        raise InvalidValueError(f"ROI {name} {value} must be {minimum} + n * {increment}")


class CameraDevice(ABC):
    # --- identity / state -------------------------------------------------
    @property
    @abstractmethod
    def camera_id(self) -> str: ...

    @property
    @abstractmethod
    def model(self) -> str: ...

    @property
    @abstractmethod
    def serial_number(self) -> str: ...

    @property
    @abstractmethod
    def ip_address(self) -> str | None: ...

    @property
    @abstractmethod
    def connected(self) -> bool: ...

    @property
    @abstractmethod
    def acquiring(self) -> bool: ...

    # --- lifecycle --------------------------------------------------------
    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None:
        """Stop acquisition if running and release the device. Safe to call when not connected."""

    @abstractmethod
    def start_acquisition(self) -> None: ...

    @abstractmethod
    def stop_acquisition(self) -> None:
        """Safe to call when not acquiring or not connected."""

    @abstractmethod
    def get_frame(self, timeout: float = 1.0) -> Frame:
        """Block up to ``timeout`` seconds for the next frame. Raises FrameTimeoutError on timeout.

        Called from the acquisition worker thread only, never from the UI loop.
        """

    # --- capabilities -----------------------------------------------------
    @abstractmethod
    def exposure_range(self) -> NumericRange | None: ...

    @abstractmethod
    def gain_range(self) -> NumericRange | None: ...

    @abstractmethod
    def frame_rate_range(self) -> NumericRange | None: ...

    @abstractmethod
    def pixel_formats(self) -> list[str]: ...

    @abstractmethod
    def roi_limits(self) -> RoiLimits | None: ...

    # --- current values ---------------------------------------------------
    @property
    @abstractmethod
    def exposure(self) -> float | None: ...

    @property
    @abstractmethod
    def gain(self) -> float | None: ...

    @property
    @abstractmethod
    def frame_rate(self) -> float | None: ...

    @property
    @abstractmethod
    def pixel_format(self) -> str: ...

    @property
    @abstractmethod
    def roi(self) -> Roi: ...

    # --- setters (validate against capabilities, raise CameraError) -------
    @abstractmethod
    def set_exposure(self, value: float) -> None: ...

    @abstractmethod
    def set_gain(self, value: float) -> None: ...

    @abstractmethod
    def set_frame_rate(self, value: float) -> None: ...

    @abstractmethod
    def set_pixel_format(self, value: str) -> None:
        """Raises InvalidStateError while acquiring."""

    @abstractmethod
    def set_roi(self, x: int, y: int, width: int, height: int) -> None:
        """Raises InvalidStateError while acquiring."""
