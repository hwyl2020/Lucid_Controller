"""Production CameraDevice backed by the LUCID Arena SDK (arena_api).

All Arena objects stay inside this module. SDK calls used here were checked against the installed
arena_api 2.7.1 source:
- system.device_infos / create_device(info) / destroy_device(device)
- device.start_stream(n) / get_buffer(timeout=<int ms>) / requeue_buffer(buf) / stop_stream()
- device.is_connected(), device.nodemap / tl_stream_nodemap .get_node(name) (ValueError if missing)
- get_buffer raises the builtin TimeoutError on timeout; other SDK errors are plain Exceptions
  whose message contains the ArenaC error name (e.g. "ACCESS_DENIED -1005")
- buffer.pdata (POINTER(uint8)), width, height, padding_x, bits_per_pixel, pixel_format (enum),
  frame_id, is_incomplete; BufferFactory.convert(buf, PixelFormat) / destroy(buf)
Node names are GenICam SFNC / LUCID stream features; optional ones are set best-effort.
"""

from __future__ import annotations

import logging
import threading
import time

import numpy as np

from app.acquisition.frame import Frame
from app.cameras import arena_sdk
from app.cameras.camera_device import (
    CameraDevice,
    CameraDisconnectedError,
    CameraError,
    CameraNotConnectedError,
    FrameTimeoutError,
    IncompleteFrameError,
    InvalidStateError,
    InvalidValueError,
    NetworkCheck,
    NumericRange,
    Roi,
    RoiLimits,
    UnsupportedFeatureError,
)
from app.cameras.camera_discovery import ArenaDeviceInfo, all_device_infos, force_ip, host_interfaces
from app.cameras.network import ForceIpPlan, reachable_interface
from app.models.features import Feature, FeatureCategory, FeatureKind, Visibility

logger = logging.getLogger(__name__)

# ~2 s of headroom at 9 FPS on a 12 MP camera (~12 MB per BayerRG8 buffer) to ride out short
# host stalls without losing frames.
DEFAULT_NUM_BUFFERS = 20
FORCE_IP_TIMEOUT_S = 5.0

# Transport-layer stream settings. OldestFirst keeps every frame in order so drops are visible as
# frame-id gaps instead of being silently replaced (recording must never drop silently).
STREAM_SETTINGS: dict[str, object] = {
    "StreamBufferHandlingMode": "OldestFirst",
    "StreamAutoNegotiatePacketSize": True,
    "StreamPacketResendEnable": True,
}

# Formats copied as-is: name -> (numpy dtype, channels, bits per pixel in the buffer).
# Bayer stays raw: copying a 12 MP BayerRG8 frame takes ~4 ms, whereas SDK conversion to RGB8 plus
# the copy took ~90 ms on a TRI122S-C, starving the stream. Display demosaics a downscaled preview.
PASSTHROUGH_FORMATS: dict[str, tuple[type, int, int]] = {
    "Mono8": (np.uint8, 1, 8),
    "Mono10": (np.uint16, 1, 16),
    "Mono12": (np.uint16, 1, 16),
    "Mono16": (np.uint16, 1, 16),
    "RGB8": (np.uint8, 3, 24),
    **{f"Bayer{p}8": (np.uint8, 1, 8) for p in ("RG", "GR", "GB", "BG")},
    **{f"Bayer{p}{b}": (np.uint16, 1, 16) for p in ("RG", "GR", "GB", "BG") for b in (10, 12, 16)},
}


class ArenaCamera(CameraDevice):
    def __init__(self, info: ArenaDeviceInfo, num_buffers: int = DEFAULT_NUM_BUFFERS) -> None:
        self._info = info
        self._num_buffers = num_buffers
        self._lock = threading.RLock()
        self._device = None
        self._acquiring = False
        self._last_frame_id: int | None = None
        self.missed_frames = 0  # frame-id gaps seen by the host
        self.incomplete_frames = 0

    # --- identity / state -------------------------------------------------
    @property
    def camera_id(self) -> str:
        return self._info.serial

    @property
    def model(self) -> str:
        return self._info.model

    @property
    def serial_number(self) -> str:
        return self._info.serial

    @property
    def ip_address(self) -> str | None:
        return self._info.ip or None

    @property
    def info(self) -> ArenaDeviceInfo:
        return self._info

    @property
    def connected(self) -> bool:
        return self._device is not None and self._is_connected()

    @property
    def acquiring(self) -> bool:
        return self._acquiring

    # --- lifecycle --------------------------------------------------------
    def connect(self) -> None:
        with self._lock:
            if self._device is not None:
                if self._is_connected():
                    return
                self._destroy_device()  # stale handle after a cable pull

            try:
                sdk = arena_sdk.load()
            except arena_sdk.ArenaSdkUnavailable as exc:
                raise CameraError(str(exc)) from exc

            try:
                infos = sdk.system.device_infos  # refreshes the SDK's list; order is not stable
            except Exception as exc:  # noqa: BLE001
                raise CameraError(f"{self.camera_id}: discovery failed: {_short(exc)}") from exc
            match = next((i for i in infos if i.get("mac") == self._info.mac), None)
            if match is None:
                raise CameraDisconnectedError(f"{self.camera_id}: not found on the network")
            self._info = ArenaDeviceInfo.from_sdk(match)  # current IP (may differ after a power cycle)

            try:
                self._device = sdk.system.create_device(match)[0]
            except Exception as exc:  # noqa: BLE001
                raise self._translate(exc, "connect") from exc

            for name, value in STREAM_SETTINGS.items():
                self._try_set(self._device.tl_stream_nodemap, name, value)
            logger.info("Opened %s S/N %s at %s", self.model, self.serial_number, self._info.ip)

    def disconnect(self) -> None:
        with self._lock:
            self.stop_acquisition()
            self._destroy_device()

    def start_acquisition(self) -> None:
        with self._lock:
            device = self._require_device()
            if self._acquiring:
                return
            self._try_set(device.nodemap, "AcquisitionMode", "Continuous")
            try:
                device.start_stream(self._num_buffers)
            except Exception as exc:  # noqa: BLE001
                raise self._translate(exc, "start acquisition") from exc
            self._acquiring = True
            self._last_frame_id = None

    def stop_acquisition(self) -> None:
        with self._lock:
            if not self._acquiring:
                return
            self._acquiring = False
            if self._device is not None:
                try:
                    self._device.stop_stream()
                except Exception as exc:  # noqa: BLE001 - device may already be gone
                    logger.warning("%s: stop_stream failed: %s", self.camera_id, _short(exc))

    def get_frame(self, timeout: float = 1.0) -> Frame:
        device = self._device
        if device is None:
            raise CameraNotConnectedError(f"{self.camera_id}: not connected")
        if not self._acquiring:
            raise InvalidStateError(f"{self.camera_id}: acquisition not started")

        try:
            buffer = device.get_buffer(timeout=max(1, int(timeout * 1000)))
        except TimeoutError as exc:
            if not self._is_connected():
                raise CameraDisconnectedError(f"{self.camera_id}: device lost") from exc
            raise FrameTimeoutError(f"{self.camera_id}: no frame within {timeout:.3f}s") from exc
        except Exception as exc:  # noqa: BLE001
            raise self._translate(exc, "get frame") from exc

        try:
            if buffer.is_incomplete:
                self.incomplete_frames += 1
                if self.incomplete_frames == 1 or self.incomplete_frames % 100 == 0:
                    logger.warning("%s: %d incomplete frame(s) discarded", self.camera_id, self.incomplete_frames)
                raise IncompleteFrameError(f"{self.camera_id}: incomplete frame")
            frame_id = int(buffer.frame_id)
            self._track_gaps(frame_id)
            data, pixel_format = self._extract(buffer)
            return Frame(
                camera_id=self.camera_id,
                frame_id=frame_id,
                timestamp=time.time(),
                width=data.shape[1],
                height=data.shape[0],
                pixel_format=pixel_format,
                data=data,
            )
        finally:
            try:
                device.requeue_buffer(buffer)
            except Exception as exc:  # noqa: BLE001
                logger.debug("%s: requeue failed: %s", self.camera_id, _short(exc))

    # --- capabilities -----------------------------------------------------
    def exposure_range(self) -> NumericRange | None:
        return self._range("ExposureTime")

    def gain_range(self) -> NumericRange | None:
        return self._range("Gain")

    def frame_rate_range(self) -> NumericRange | None:
        return self._range("AcquisitionFrameRate")

    def pixel_formats(self) -> list[str]:
        node = self._readable("PixelFormat")
        if node is None:
            return []
        try:
            return [name for name, entry in node.enumentry_nodes.items() if entry.is_readable]
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s: could not list pixel formats: %s", self.camera_id, _short(exc))
            return []

    def roi_limits(self) -> RoiLimits | None:
        width, height = self._readable("Width"), self._readable("Height")
        if width is None or height is None:
            return None
        width_max, height_max = self._readable("WidthMax"), self._readable("HeightMax")
        offset_x, offset_y = self._readable("OffsetX"), self._readable("OffsetY")
        return RoiLimits(
            sensor_width=int(width_max.value if width_max else width.max),
            sensor_height=int(height_max.value if height_max else height.max),
            min_width=int(width.min),
            min_height=int(height.min),
            width_increment=int(width.inc or 1),
            height_increment=int(height.inc or 1),
            offset_x_increment=int(offset_x.inc or 1) if offset_x else 1,
            offset_y_increment=int(offset_y.inc or 1) if offset_y else 1,
        )

    # --- current values ---------------------------------------------------
    @property
    def exposure(self) -> float | None:
        return self._value("ExposureTime")

    @property
    def gain(self) -> float | None:
        return self._value("Gain")

    @property
    def frame_rate(self) -> float | None:
        return self._value("AcquisitionFrameRate")

    @property
    def pixel_format(self) -> str:
        return str(self._value("PixelFormat") or "")

    @property
    def roi(self) -> Roi:
        return Roi(
            int(self._value("OffsetX") or 0),
            int(self._value("OffsetY") or 0),
            int(self._value("Width") or 0),
            int(self._value("Height") or 0),
        )

    # --- setters ----------------------------------------------------------
    def set_exposure(self, value: float) -> None:
        with self._lock:
            self._set_auto_off("ExposureAuto")
            self._set_numeric("ExposureTime", float(value), "exposure")

    def set_gain(self, value: float) -> None:
        with self._lock:
            self._set_auto_off("GainAuto")
            self._set_numeric("Gain", float(value), "gain")

    def set_frame_rate(self, value: float) -> None:
        with self._lock:
            device = self._require_device()
            # On LUCID cameras the frame rate is only writable once explicitly enabled.
            self._try_set(device.nodemap, "AcquisitionFrameRateEnable", True)
            self._set_numeric("AcquisitionFrameRate", float(value), "frame rate")

    def set_pixel_format(self, value: str) -> None:
        with self._lock:
            device = self._require_device()
            self._require_not_acquiring("pixel format")
            available = self.pixel_formats()
            if value not in available:
                raise InvalidValueError(f"Pixel format {value!r} not available; camera offers {available}")
            self._write(device.nodemap, "PixelFormat", value)

    def set_roi(self, x: int, y: int, width: int, height: int) -> None:
        with self._lock:
            device = self._require_device()
            self._require_not_acquiring("ROI")
            limits = self.roi_limits()
            if limits is None:
                raise UnsupportedFeatureError(f"{self.camera_id}: ROI not supported")
            limits.validate(Roi(x, y, width, height))
            # Offsets first to 0 so the new size always fits, then size, then the real offsets.
            nodemap = device.nodemap
            for name in ("OffsetX", "OffsetY"):
                if self._writable(name) is not None:
                    self._write(nodemap, name, 0)
            self._write(nodemap, "Width", int(width))
            self._write(nodemap, "Height", int(height))
            if x:
                self._write(nodemap, "OffsetX", int(x))
            if y:
                self._write(nodemap, "OffsetY", int(y))

    # --- generic feature access (Property Grid) ----------------------------
    def feature_tree(self) -> FeatureCategory | None:
        """Walk the device node map from its ``Root`` category (GenICam), reading every feature."""
        with self._lock:
            device = self._require_device()
            root = self._node(device.nodemap, "Root")
            if root is None:
                return None
            return self._category(root, depth=0)

    def write_feature(self, name: str, value: object) -> None:
        with self._lock:
            device = self._require_device()
            node = self._node(device.nodemap, name)
            if node is None:
                raise UnsupportedFeatureError(f"{self.camera_id}: feature {name} not present")
            kind = node.interface_type.name
            if not node.is_writable:
                hint = " (stop acquisition to change it)" if self._acquiring else ""
                raise InvalidStateError(f"{name} is read-only in the camera's current state{hint}")
            converted = _convert_for_node(node, kind, value, name)
            try:
                node.value = converted
            except Exception as exc:  # noqa: BLE001
                raise self._translate(exc, f"set {name}={converted!r}") from exc

    def execute_feature(self, name: str) -> None:
        with self._lock:
            device = self._require_device()
            node = self._node(device.nodemap, name)
            if node is None or node.interface_type.name != "COMMAND":
                raise UnsupportedFeatureError(f"{self.camera_id}: command {name} not present")
            if not node.is_writable:
                raise InvalidStateError(f"{name} cannot be executed in the camera's current state")
            try:
                node.execute()
            except Exception as exc:  # noqa: BLE001
                raise self._translate(exc, f"execute {name}") from exc

    def _category(self, node, depth: int) -> FeatureCategory:
        features: list[Feature] = []
        subcategories: list[FeatureCategory] = []
        try:
            children = node.features
        except Exception as exc:  # noqa: BLE001
            logger.debug("%s: cannot list category %s: %s", self.camera_id, node.name, _short(exc))
            children = {}
        for child in children.values():
            try:
                kind = child.interface_type.name
            except Exception:  # noqa: BLE001
                continue
            if kind == "CATEGORY":
                if depth < MAX_CATEGORY_DEPTH:
                    subcategories.append(self._category(child, depth + 1))
            elif kind in _FEATURE_KINDS:
                features.append(_feature_from_node(child, kind))
        return FeatureCategory(node.name, _display_name(node), tuple(features), tuple(subcategories))

    # --- frame extraction -------------------------------------------------
    def _extract(self, buffer) -> tuple[np.ndarray, str]:
        name = buffer.pixel_format.name
        spec = PASSTHROUGH_FORMATS.get(name)
        if spec is not None and int(buffer.bits_per_pixel) == spec[2]:
            return _copy_pixels(buffer, spec[0], spec[1]), name

        # Everything else (packed formats, BGR, YUV, ...) is converted by the SDK itself, which
        # knows the exact packing. Slow at high resolution; prefer a passthrough format.
        target = "Mono8" if name.startswith("Mono") else "RGB8"
        sdk = arena_sdk.load()
        try:
            converted = sdk.buffer_factory.convert(buffer, sdk.enums.PixelFormat[target])
        except Exception as exc:  # noqa: BLE001
            raise CameraError(f"{self.camera_id}: cannot convert {name} to {target}: {_short(exc)}") from exc
        try:
            dtype, channels, _ = PASSTHROUGH_FORMATS[target]
            return _copy_pixels(converted, dtype, channels), target
        finally:
            sdk.buffer_factory.destroy(converted)

    def _track_gaps(self, frame_id: int) -> None:
        last = self._last_frame_id
        self._last_frame_id = frame_id
        if last is not None and frame_id > last + 1:
            missed = frame_id - last - 1
            self.missed_frames += missed
            logger.warning(
                "%s: %d frame(s) missed before #%d (total %d)", self.camera_id, missed, frame_id, self.missed_frames
            )

    # --- node helpers -----------------------------------------------------
    def _node(self, nodemap, name: str):
        try:
            return nodemap.get_node(name)
        except (ValueError, KeyError):
            return None

    def _readable(self, name: str):
        device = self._device
        if device is None:
            return None
        node = self._node(device.nodemap, name)
        try:
            return node if node is not None and node.is_readable else None
        except Exception:  # noqa: BLE001
            return None

    def _writable(self, name: str):
        device = self._device
        if device is None:
            return None
        node = self._node(device.nodemap, name)
        try:
            return node if node is not None and node.is_writable else None
        except Exception:  # noqa: BLE001
            return None

    def _value(self, name: str):
        node = self._readable(name)
        if node is None:
            return None
        try:
            return node.value
        except Exception as exc:  # noqa: BLE001
            logger.debug("%s: reading %s failed: %s", self.camera_id, name, _short(exc))
            return None

    def _range(self, name: str) -> NumericRange | None:
        node = self._readable(name)
        if node is None:
            return None
        try:
            inc = node.inc
            return NumericRange(float(node.min), float(node.max), float(inc) if inc else None)
        except Exception as exc:  # noqa: BLE001
            logger.debug("%s: range of %s unavailable: %s", self.camera_id, name, _short(exc))
            return None

    def _set_numeric(self, name: str, value: float, label: str) -> None:
        device = self._require_device()
        if self._writable(name) is None:
            raise UnsupportedFeatureError(f"{self.camera_id}: {label} is not writable on this camera")
        value_range = self._range(name)
        if value_range is not None:
            value_range.validate(value, label)
        self._write(device.nodemap, name, value)

    def _set_auto_off(self, name: str) -> None:
        node = self._writable(name)
        if node is not None and node.value != "Off":
            self._write(self._device.nodemap, name, "Off")

    def _write(self, nodemap, name: str, value) -> None:
        node = self._node(nodemap, name)
        if node is None:
            raise UnsupportedFeatureError(f"{self.camera_id}: node {name} not present")
        try:
            node.value = value
        except Exception as exc:  # noqa: BLE001
            raise self._translate(exc, f"set {name}={value!r}") from exc

    def _try_set(self, nodemap, name: str, value) -> None:
        """Best-effort write for optional features: log and continue on failure."""
        node = self._node(nodemap, name)
        if node is None:
            logger.info("%s: optional node %s not present", self.camera_id, name)
            return
        try:
            if node.is_writable:
                node.value = value
            else:
                logger.info("%s: optional node %s not writable", self.camera_id, name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s: setting %s=%r failed: %s", self.camera_id, name, value, _short(exc))

    # --- state helpers ----------------------------------------------------
    def _require_device(self):
        if self._device is None:
            raise CameraNotConnectedError(f"{self.camera_id}: not connected")
        if not self._is_connected():
            raise CameraDisconnectedError(f"{self.camera_id}: device lost")
        return self._device

    def _require_not_acquiring(self, what: str) -> None:
        if self._acquiring:
            raise InvalidStateError(f"{self.camera_id}: stop acquisition before changing {what}")

    def _is_connected(self) -> bool:
        try:
            return bool(self._device is not None and self._device.is_connected())
        except Exception:  # noqa: BLE001
            return False

    # --- network (Force IP) -------------------------------------------------
    def network_check(self) -> NetworkCheck:
        """Re-discover this camera (by MAC) and compare its subnet with the host adapters."""
        devices = all_device_infos()
        mine = next((d for d in devices if d.mac == self._info.mac), None)
        if mine is None:
            raise CameraDisconnectedError(f"{self.camera_id}: not found on the network")
        self._info = mine  # IP may have changed (power cycle, Force IP)
        interfaces = host_interfaces()
        used = {d.ip for d in devices if d.mac != mine.mac} | {i.ip for i in interfaces}
        reachable = reachable_interface(mine.ip, interfaces) is not None
        return NetworkCheck(mine.ip, mine.subnet_mask, reachable, tuple(interfaces), frozenset(used))

    def force_ip(self, plan: ForceIpPlan, timeout_s: float = FORCE_IP_TIMEOUT_S) -> None:
        if self._device is not None:
            raise InvalidStateError(f"{self.camera_id}: turn the camera off before forcing its IP")
        old_ip = self._info.ip
        force_ip(self._info.mac, plan.ip, plan.subnet_mask, plan.gateway)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:  # the camera re-announces itself with the new address
            mine = next((d for d in all_device_infos(500) if d.mac == self._info.mac), None)
            if mine is not None and mine.ip == plan.ip:
                self._info = mine
                logger.info("%s: IP forced %s -> %s (adapter %s)", self.camera_id, old_ip, plan.ip, plan.interface)
                return
        raise CameraError(f"{self.camera_id}: camera did not take IP {plan.ip} within {timeout_s:.0f}s")

    def _destroy_device(self) -> None:
        device, self._device = self._device, None
        self._acquiring = False
        if device is None:
            return
        try:
            arena_sdk.load().system.destroy_device(device)
            logger.info("Closed %s S/N %s", self.model, self.serial_number)
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s: destroy_device failed: %s", self.camera_id, _short(exc))

    def _translate(self, exc: Exception, action: str) -> CameraError:
        if isinstance(exc, CameraError):
            return exc
        message = _short(exc)
        if isinstance(exc, TimeoutError):
            return FrameTimeoutError(f"{self.camera_id}: {action} timed out")
        if self._device is not None and not self._is_connected():
            return CameraDisconnectedError(f"{self.camera_id}: device lost during {action}")
        if "ACCESS_DENIED" in message:
            return CameraError(
                f"{self.camera_id}: access denied during {action}; the camera may be open in "
                "another application (e.g. ArenaView) or on another PC"
            )
        if "INVALID_ADDRESS" in message:
            return CameraError(
                f"{self.camera_id}: cannot reach the camera at {self._info.ip}; it is probably on a different "
                "subnet than this PC's network adapter (e.g. a 169.254.x.x link-local address after a power cycle). "
                "Give the camera and the adapter addresses in the same subnet (ArenaView: Force IP / persistent IP)"
            )
        if isinstance(exc, ValueError):
            return InvalidValueError(f"{self.camera_id}: {action}: {message}")
        return CameraError(f"{self.camera_id}: {action} failed: {message}")


def _copy_pixels(buffer, dtype: type, channels: int) -> np.ndarray:
    """Copy image bytes out of an SDK buffer into an owned numpy array (honours row padding)."""
    width, height = int(buffer.width), int(buffer.height)
    bytes_per_pixel = int(buffer.bits_per_pixel) // 8
    row_bytes = width * bytes_per_pixel
    stride = row_bytes + int(buffer.padding_x or 0)
    raw = np.ctypeslib.as_array(buffer.pdata, shape=(stride * height,))
    rows = raw.reshape(height, stride)[:, :row_bytes]
    pixels = np.ascontiguousarray(rows).view(dtype)
    shape = (height, width) if channels == 1 else (height, width, channels)
    return pixels.reshape(shape).copy()


def _short(exc: BaseException, limit: int = 240) -> str:
    """Arena error messages are multi-line banners; collapse to one line."""
    text = " ".join(str(exc).split()) or type(exc).__name__
    return text if len(text) <= limit else text[: limit - 3] + "..."


# --- GenICam node -> app Feature ------------------------------------------------
MAX_CATEGORY_DEPTH = 8
_FEATURE_KINDS = {
    "INTEGER": FeatureKind.INTEGER,
    "FLOAT": FeatureKind.FLOAT,
    "BOOLEAN": FeatureKind.BOOLEAN,
    "ENUMERATION": FeatureKind.ENUMERATION,
    "STRING": FeatureKind.STRING,
    "COMMAND": FeatureKind.COMMAND,
    "REGISTER": FeatureKind.REGISTER,
}
_VISIBILITY = {v.name: v for v in Visibility}


def _display_name(node) -> str:
    try:
        return node.display_name or node.name
    except Exception:  # noqa: BLE001
        return node.name


def _feature_from_node(node, kind_name: str) -> Feature:
    """Read one node into a Feature; failures are recorded on the feature, never raised."""
    kind = _FEATURE_KINDS[kind_name]
    fields: dict = {}
    error = None
    try:
        access = node.access_mode.name
        access = access if access in ("RW", "RO", "WO", "NA", "NI") else "NA"
        fields["visibility"] = _VISIBILITY.get(node.visibility.name, Visibility.BEGINNER)
        try:
            fields["description"] = (node.tool_tip or node.description or "").strip()
        except Exception:  # noqa: BLE001
            fields["description"] = ""
        if access in ("RO", "RW") and kind not in (FeatureKind.COMMAND, FeatureKind.REGISTER):
            fields["value"] = node.value
            if kind in (FeatureKind.INTEGER, FeatureKind.FLOAT):
                fields["minimum"], fields["maximum"] = node.min, node.max
                fields["increment"] = node.inc
                try:
                    fields["unit"] = node.unit or ""
                except Exception:  # noqa: BLE001
                    fields["unit"] = ""
            elif kind is FeatureKind.ENUMERATION:
                fields["entries"] = tuple(n for n, e in node.enumentry_nodes.items() if e.is_readable)
    except Exception as exc:  # noqa: BLE001 - one bad node must not break the grid
        error = _short(exc)
        access = locals().get("access", "NA")
    return Feature(name=node.name, display_name=_display_name(node), kind=kind, access=access, error=error, **fields)


def _convert_for_node(node, kind_name: str, value: object, name: str):
    """Validate/convert a UI value against the node's live limits before writing."""
    try:
        if kind_name == "INTEGER":
            number = int(value)
            NumericRange(node.min, node.max, node.inc or None).validate(number, name)
            return number
        if kind_name == "FLOAT":
            number = float(value)
            NumericRange(node.min, node.max).validate(number, name)
            return number
        if kind_name == "BOOLEAN":
            return bool(value)
        if kind_name == "ENUMERATION":
            available = [n for n, e in node.enumentry_nodes.items() if e.is_readable]
            if str(value) not in available:
                raise InvalidValueError(f"{name}: {value!r} is not one of {available}")
            return str(value)
        if kind_name == "STRING":
            return str(value)
    except (TypeError, ValueError) as exc:
        raise InvalidValueError(f"{name}: invalid value {value!r} ({exc})") from exc
    raise UnsupportedFeatureError(f"{name}: {kind_name.lower()} features cannot be written here")
