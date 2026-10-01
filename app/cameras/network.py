"""Pure GigE addressing rules for Force IP (no SDK calls; unit-testable).

A camera can only be opened from a host adapter on the same IPv4 subnet. After a power cycle a LUCID
camera may come back on a link-local 169.254.x.x address; Force IP (GigE Vision) temporarily moves it
onto an adapter's subnet until the camera reboots.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

NO_GATEWAY = "0.0.0.0"


@dataclass(frozen=True)
class HostInterface:
    ip: str
    subnet_mask: str
    mac: str = ""

    @property
    def network(self) -> ipaddress.IPv4Network:
        return ipaddress.IPv4Network(f"{self.ip}/{self.subnet_mask}", strict=False)

    def __str__(self) -> str:
        return f"{self.ip}/{self.network.prefixlen}"


@dataclass(frozen=True)
class ForceIpPlan:
    interface: HostInterface
    ip: str
    subnet_mask: str
    gateway: str = NO_GATEWAY


def reachable_interface(camera_ip: str, interfaces: list[HostInterface]) -> HostInterface | None:
    """The host adapter whose subnet contains the camera's address, if any."""
    try:
        address = ipaddress.IPv4Address(camera_ip)
    except ValueError:
        return None
    return next((i for i in interfaces if _valid(i) and address in i.network), None)


def plan_force_ip(camera_ip: str, interface: HostInterface, used_ips: set[str]) -> ForceIpPlan:
    """Choose a free address for the camera on ``interface``'s subnet.

    Keeps the camera's current host number when possible (169.254.92.34 -> 172.16.1.34), otherwise
    takes the first free address after the adapter's. Never picks the network/broadcast address,
    the adapter's own address or any address in ``used_ips``.
    """
    network = interface.network
    hosts_count = network.num_addresses - 2
    if hosts_count < 2:
        raise ValueError(f"Subnet {network} of adapter {interface.ip} has no room for a camera")
    taken = {interface.ip, *used_ips}

    def candidate(host_number: int) -> str | None:
        if 1 <= host_number <= hosts_count:
            address = str(network.network_address + host_number)
            return None if address in taken else address
        return None

    try:
        host_mask = int(network.hostmask)
        preferred = candidate(int(ipaddress.IPv4Address(camera_ip)) & host_mask)
    except ValueError:
        preferred = None
    if preferred:
        return ForceIpPlan(interface, preferred, str(network.netmask))
    start = int(ipaddress.IPv4Address(interface.ip)) - int(network.network_address)
    for offset in range(1, hosts_count + 1):
        address = candidate((start - 1 + offset) % hosts_count + 1)
        if address:
            return ForceIpPlan(interface, address, str(network.netmask))
    raise ValueError(f"No free address left on {network}")


def _valid(interface: HostInterface) -> bool:
    return interface.ip not in ("", "0.0.0.0") and interface.subnet_mask not in ("", "0.0.0.0")
