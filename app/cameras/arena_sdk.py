"""Single access point to the Arena SDK Python package.

Importing ``arena_api.system`` opens the SDK system singleton, so the import is deferred until a
camera feature is actually used. Tests replace ``load`` with a fake SDK.

``SYSTEM_LOCK`` serialises system-level calls (device_infos, create_device, force_ip,
interface_infos): background discovery runs while cameras are being opened or re-addressed, and
device_infos also rebuilds the list that create_device picks from.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from types import ModuleType
from typing import Any


@dataclass(frozen=True)
class ArenaSdk:
    system: Any  # arena_api.system.system
    buffer_factory: Any  # arena_api.buffer.BufferFactory
    enums: ModuleType  # arena_api.enums


class ArenaSdkUnavailable(RuntimeError):
    pass


_sdk: ArenaSdk | None = None
SYSTEM_LOCK = threading.RLock()


def load() -> ArenaSdk:
    global _sdk
    if _sdk is None:
        try:
            from arena_api import enums
            from arena_api.buffer import BufferFactory
            from arena_api.system import system
        except Exception as exc:  # noqa: BLE001 - ImportError or SDK/driver load failure
            raise ArenaSdkUnavailable(f"Arena SDK could not be loaded: {exc}") from exc
        _sdk = ArenaSdk(system=system, buffer_factory=BufferFactory, enums=enums)
    return _sdk
