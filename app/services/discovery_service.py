"""Hot-plug discovery: cameras connected while the app runs appear without a restart.

A background thread repeats the GigE Vision discovery every ``interval`` seconds and adds cameras
it has not seen before (by serial number) to the CameraManager; the UI (sidebar, multiview, status
panel, log filter) picks up the new camera ids on its next frame. Known cameras are never removed or
touched here: a camera that disappears keeps its row, and opening it reports "not found" until it
is back (auto-reconnect handles cameras lost while streaming).

Discovery is a small broadcast plus a short wait, done off the UI thread; SDK system calls are
serialised with camera connects and Force IP through ``arena_sdk.SYSTEM_LOCK``.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from app.cameras.camera_device import CameraDevice, CameraError
from app.cameras.camera_manager import CameraManager
from app.camera_log import for_camera

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_S = 3.0
DISCOVERY_TIMEOUT_MS = 400


def arena_scan() -> list[CameraDevice]:
    """One discovery round over the Arena SDK, as (not yet opened) ArenaCamera objects."""
    from app.cameras.arena_camera import ArenaCamera  # noqa: PLC0415 - keeps the service SDK-agnostic
    from app.cameras.camera_discovery import all_device_infos  # noqa: PLC0415

    return [ArenaCamera(info) for info in all_device_infos(DISCOVERY_TIMEOUT_MS) if info.serial]


class DiscoveryService:
    def __init__(self, manager: CameraManager, scan: Callable[[], list[CameraDevice]] = arena_scan,
                 interval: float = DEFAULT_INTERVAL_S) -> None:
        self._manager = manager
        self._scan = scan
        self._interval = interval
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = threading.Thread(target=self._run, name="discovery", daemon=True)
        self._failing = False
        self.enabled = True

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)

    def scan_now(self) -> None:
        """Ask for a discovery round right away (Cameras > Scan for cameras)."""
        self._wake.set()

    def scan_once(self) -> list[str]:
        """Run one discovery round; returns the ids of the cameras added."""
        try:
            found = self._scan()
        except CameraError as exc:
            if not self._failing:  # log a failing network once, not every few seconds
                logger.warning("Camera discovery failed: %s", exc)
            self._failing = True
            return []
        self._failing = False
        known = set(self._manager.camera_ids)
        added = []
        for camera in found:
            if camera.camera_id in known:
                continue
            try:
                self._manager.add_camera(camera)
            except ValueError:  # added concurrently
                continue
            known.add(camera.camera_id)
            added.append(camera.camera_id)
            logger.info("New camera detected: %s S/N %s at %s", camera.model, camera.serial_number,
                        camera.ip_address or "no IP", extra=for_camera(camera.camera_id))
        return added

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self._interval)
            self._wake.clear()
            if self._stop.is_set():
                break
            if self.enabled:
                self.scan_once()
