"""Force IP workflow for the UI: check reachability, pick the adapter, force a free address.

Adapter choice: the camera must be moved onto the subnet of the adapter it is physically attached
to, which the SDK does not report. Wired adapters are preferred over wireless ones (GigE cameras are
wired); if more than one candidate remains the UI asks the user.
"""

from __future__ import annotations

import logging

import psutil

from app.cameras.camera_device import NetworkCheck
from app.cameras.camera_manager import CameraManager
from app.cameras.network import ForceIpPlan, HostInterface, plan_force_ip

logger = logging.getLogger(__name__)

WIRELESS_HINTS = ("wi-fi", "wifi", "wlan", "wireless", "802.11")


class NetworkService:
    def __init__(self, manager: CameraManager) -> None:
        self._manager = manager

    def check(self, camera_id: str) -> NetworkCheck | None:
        """None for cameras without a network (e.g. simulators)."""
        return self._manager.camera(camera_id).network_check()

    def candidate_interfaces(self, check: NetworkCheck) -> list[HostInterface]:
        """Adapters the camera could be moved to: wired ones if any, otherwise all."""
        wireless_macs = _wireless_macs()
        wired = [i for i in check.interfaces if _norm_mac(i.mac) not in wireless_macs]
        return wired or list(check.interfaces)

    def plan(self, check: NetworkCheck, interface: HostInterface) -> ForceIpPlan:
        return plan_force_ip(check.camera_ip, interface, set(check.used_ips))

    def force(self, camera_id: str, plan: ForceIpPlan) -> None:
        self._manager.camera(camera_id).force_ip(plan)


def _norm_mac(mac: str) -> str:
    return mac.replace("-", ":").lower()


def _wireless_macs() -> set[str]:
    """MAC addresses of wireless adapters, judged by adapter name (e.g. "Wi-Fi")."""
    macs = set()
    try:
        for name, addresses in psutil.net_if_addrs().items():
            if any(hint in name.lower() for hint in WIRELESS_HINTS):
                macs |= {_norm_mac(a.address) for a in addresses if a.family == psutil.AF_LINK}
    except Exception as exc:  # noqa: BLE001 - adapter names are a hint only
        logger.debug("Could not read adapter names: %s", exc)
    return macs
