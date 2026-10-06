"""Hot-plug discovery: cameras connected while the app runs appear, and unplugged cameras go away,
without a restart.

A background thread repeats the GigE Vision discovery every ``interval`` seconds:
- cameras it has not seen before (by serial number) are added to the CameraManager;
- a camera it manages (found by discovery, or adopted at startup) that stays missing for
  ``missing_scans`` rounds in a row and is not delivering frames is reported in ``take_gone()``;
  the UI thread removes it (``AppServices.remove_camera``). A camera that is still streaming is
  never removed (a missed discovery reply must not drop a working camera); when its cable is
  pulled the stream fails within ~1 s, and it is removed on the next round.
Cameras added by other means (simulators) are never touched.

The UI (sidebar, multiview, status panel, log filter) follows ``CameraManager.camera_ids``.
Discovery is a small broadcast plus a short wait, done off the UI thread; SDK system calls are
serialised with camera connects and Force IP through ``arena_sdk.SYSTEM_LOCK``.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable

from app.cameras.camera_device import CameraDevice, CameraError
from app.cameras.camera_manager import CameraManager
from app.models.camera_state import CameraState
from app.camera_log import for_camera

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_S = 2.0
MISSING_SCANS = 3  # ~6 s of absence before an idle camera is removed (one missed reply is not enough)
DISCOVERY_TIMEOUT_MS = 400


def arena_scan() -> list[CameraDevice]:
    """One discovery round over the Arena SDK, as (not yet opened) ArenaCamera objects."""
    from app.cameras.arena_camera import ArenaCamera  # noqa: PLC0415 - keeps the service SDK-agnostic
    from app.cameras.camera_discovery import all_device_infos  # noqa: PLC0415

    return [ArenaCamera(info) for info in all_device_infos(DISCOVERY_TIMEOUT_MS) if info.serial]


class DiscoveryService:
    def __init__(self, manager: CameraManager, scan: Callable[[], list[CameraDevice]] = arena_scan,
                 interval: float = DEFAULT_INTERVAL_S, missing_scans: int = MISSING_SCANS) -> None:
        self._manager = manager
        self._scan = scan
        self._interval = interval
        self._missing_scans = missing_scans
        self._lock = threading.Lock()
        self._managed: set[str] = set()  # camera ids this service may remove
        self._missing: dict[str, int] = {}  # camera id -> consecutive rounds not found
        self._gone: list[str] = []  # waiting for the UI thread to remove them
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

    def adopt(self, camera_ids: Iterable[str]) -> None:
        """Manage cameras found before the service started (startup discovery)."""
        with self._lock:
            self._managed.update(camera_ids)

    def scan_now(self) -> None:
        """Ask for a discovery round right away (Cameras > Scan for cameras now)."""
        self._wake.set()

    def take_gone(self) -> list[str]:
        """Cameras that have disappeared from the network; the caller removes them."""
        with self._lock:
            gone, self._gone = self._gone, []
        return gone

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
        found_ids = {camera.camera_id for camera in found}
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
        with self._lock:
            self._managed.update(added)
            self._managed &= known  # forget cameras removed elsewhere
            for camera_id in self._managed:
                if camera_id in found_ids:
                    self._missing.pop(camera_id, None)
                    continue
                self._missing[camera_id] = self._missing.get(camera_id, 0) + 1
                if (self._missing[camera_id] >= self._missing_scans and camera_id not in self._gone
                        and not self._delivering(camera_id)):
                    self._gone.append(camera_id)
                    logger.info("Camera disconnected from the network", extra=for_camera(camera_id))
            for camera_id in self._gone:
                self._managed.discard(camera_id)
                self._missing.pop(camera_id, None)
        return added

    def _delivering(self, camera_id: str) -> bool:
        try:
            return self._manager.state(camera_id) is CameraState.ACQUIRING
        except KeyError:
            return False

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self._interval)
            self._wake.clear()
            if self._stop.is_set():
                break
            if self.enabled:
                try:
                    self.scan_once()
                except Exception:  # noqa: BLE001 - never let the watcher die
                    logger.exception("Camera discovery error")
