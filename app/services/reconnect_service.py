"""Automatic reconnect for cameras lost while in use (cable pull, PoE reset, network glitch).

A background thread watches for cameras in ERROR whose failure was a CameraDisconnectedError and
retries ``CameraManager.reconnect`` with exponential backoff. Streaming resumes only for cameras the
user had streaming; an active recording continues because recording queues persist on the manager.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from app.cameras.camera_device import CameraDisconnectedError, CameraError
from app.cameras.camera_manager import CameraManager
from app.models.camera_state import CameraState
from app.camera_log import for_camera

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReconnectState:
    attempts: int
    next_attempt_in: float
    last_failure: str | None


class ReconnectService:
    def __init__(
        self,
        manager: CameraManager,
        initial_delay: float = 2.0,
        max_delay: float = 30.0,
        poll_interval: float = 0.5,
    ) -> None:
        self._manager = manager
        self._initial_delay = initial_delay
        self._max_delay = max_delay
        self._poll_interval = poll_interval
        self._lock = threading.Lock()
        self._pending: dict[str, dict] = {}  # camera_id -> {attempts, delay, next_at, last_failure}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="reconnect", daemon=True)
        self.enabled = True

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=10)

    def state(self, camera_id: str) -> ReconnectState | None:
        """Reconnect progress for the UI, or None if the camera is not being reconnected."""
        with self._lock:
            entry = self._pending.get(camera_id)
            if entry is None:
                return None
            return ReconnectState(
                attempts=entry["attempts"],
                next_attempt_in=max(0.0, entry["next_at"] - time.monotonic()),
                last_failure=entry["last_failure"],
            )

    def poll_once(self) -> None:
        """One pass over all cameras (the background thread calls this; tests may too)."""
        now = time.monotonic()
        for camera_id in self._manager.camera_ids:
            try:
                should = self._should_reconnect(camera_id)
            except KeyError:  # removed (unplugged) meanwhile
                continue
            if not should:
                with self._lock:
                    self._pending.pop(camera_id, None)
                continue
            with self._lock:
                entry = self._pending.setdefault(
                    camera_id,
                    {"attempts": 0, "delay": self._initial_delay, "next_at": now + self._initial_delay, "last_failure": None},
                )
                if now < entry["next_at"]:
                    continue
                entry["attempts"] += 1
                attempt = entry["attempts"]
            logger.info("Reconnecting %s (attempt %d)", camera_id, attempt, extra=for_camera(camera_id))
            try:
                self._manager.reconnect(camera_id)
            except CameraError as exc:
                with self._lock:
                    entry["last_failure"] = str(exc)
                    # Exponential backoff from at least 0.1 s, so a 0 initial delay still backs off.
                    base = max(self._initial_delay, 0.1)
                    entry["delay"] = min(base * 2 ** (attempt - 1), self._max_delay)
                    entry["next_at"] = time.monotonic() + entry["delay"]
                logger.info("Reconnect of %s failed (%s); retrying in %.0fs", camera_id, exc, entry["delay"], extra=for_camera(camera_id))
            else:
                with self._lock:
                    self._pending.pop(camera_id, None)

    def _should_reconnect(self, camera_id: str) -> bool:
        return (
            self.enabled
            and self._manager.state(camera_id) is CameraState.ERROR
            and isinstance(self._manager.last_exception(camera_id), CameraDisconnectedError)
            and self._manager.wants_streaming(camera_id)
        )

    def _run(self) -> None:
        while not self._stop.wait(self._poll_interval):
            try:
                self.poll_once()
            except Exception:  # noqa: BLE001 - never let the watcher die
                logger.exception("Reconnect watcher error")
