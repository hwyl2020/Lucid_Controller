"""Discovery of LUCID cameras through the Arena SDK."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.cameras import arena_sdk
from app.cameras.camera_device import CameraError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ArenaDeviceInfo:
    model: str
    serial: str
    mac: str
    ip: str
    subnet_mask: str
    firmware: str
    vendor: str
    user_name: str

    @classmethod
    def from_sdk(cls, info: dict) -> ArenaDeviceInfo:
        # Keys documented on arena_api.system.device_infos.
        return cls(
            model=info.get("model", ""),
            serial=info.get("serial", ""),
            mac=info.get("mac", ""),
            ip=info.get("ip", ""),
            subnet_mask=info.get("subnetmask", ""),
            firmware=info.get("version", ""),
            vendor=info.get("vendor", ""),
            user_name=info.get("name", ""),
        )


def discover_arena_cameras(timeout_ms: int = 1000) -> list[ArenaDeviceInfo]:
    """Broadcast a GigE Vision discovery and return the responding devices.

    Returns an empty list (with a warning) if the SDK is not installed. Raises CameraError if the
    SDK is present but discovery fails.
    """
    try:
        sdk = arena_sdk.load()
    except arena_sdk.ArenaSdkUnavailable as exc:
        logger.warning("%s; Arena cameras unavailable", exc)
        return []
    try:
        sdk.system.DEVICE_INFOS_TIMEOUT_MILLISEC = int(timeout_ms)
        infos = [ArenaDeviceInfo.from_sdk(info) for info in sdk.system.device_infos]
    except Exception as exc:  # noqa: BLE001 - SDK raises plain Exception
        raise CameraError(f"Camera discovery failed: {exc}") from exc
    for info in infos:
        logger.info("Discovered %s S/N %s at %s (fw %s)", info.model, info.serial, info.ip, info.firmware)
    if not infos:
        logger.info("No Arena cameras discovered")
    return infos
