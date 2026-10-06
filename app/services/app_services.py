"""Wiring of the application services the UI depends on."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.cameras.camera_device import CameraDevice
from app.cameras.camera_manager import CameraManager
from app.services.camera_control_service import CameraControlService
from app.services.camera_status_service import CameraStatusService
from app.services.discovery_service import DiscoveryService
from app.services.feature_service import FeatureService
from app.services.log_buffer import LogBuffer
from app.services.network_service import NetworkService
from app.services.performance_monitor import PerformanceMonitor
from app.services.profile_service import ProfileService, SettingsApplier
from app.services.reconnect_service import ReconnectService
from app.services.recording_service import RecordingService
from app.services.session_manager import SessionManager
from app.camera_log import for_camera

logger = logging.getLogger(__name__)


@dataclass
class AppServices:
    config: dict
    config_path: Path
    log_dir: Path
    manager: CameraManager
    controls: CameraControlService
    statuses: CameraStatusService
    features: FeatureService
    network: NetworkService
    logs: LogBuffer
    recording: RecordingService
    reconnect: ReconnectService
    performance: PerformanceMonitor
    profiles: ProfileService
    sessions: SessionManager
    discovery: DiscoveryService

    @classmethod
    def create(
        cls, config: dict, config_path: Path, manager: CameraManager, logs: LogBuffer | None = None,
        discover: Callable[[], list[CameraDevice]] | None = None,
    ) -> AppServices:
        """``discover``: one camera-discovery round for hot-plug detection (None = no discovery)."""
        controls = CameraControlService(manager)
        statuses = CameraStatusService(manager)
        recording = RecordingService(manager, config)
        applier = SettingsApplier(manager, controls)
        reconnect = ReconnectService(manager)
        reconnect.enabled = bool(config["reconnect"]["enabled"])
        return cls(
            config=config,
            config_path=config_path,
            log_dir=Path(config["logging"]["directory"]),
            manager=manager,
            controls=controls,
            statuses=statuses,
            features=FeatureService(manager, recording.is_recording),
            network=NetworkService(manager),
            logs=logs if logs is not None else LogBuffer(),
            recording=recording,
            reconnect=reconnect,
            performance=PerformanceMonitor(manager, recording, statuses=statuses),
            profiles=ProfileService(manager, applier, Path(config["profiles"]["directory"])),
            sessions=SessionManager(manager, applier, Path(config["sessions"]["directory"])),
            discovery=cls._discovery(manager, discover),
        )

    @staticmethod
    def _discovery(manager: CameraManager, discover) -> DiscoveryService:
        service = DiscoveryService(manager, discover) if discover else DiscoveryService(manager)
        service.enabled = discover is not None
        return service

    def remove_camera(self, camera_id: str) -> None:
        """Take an unplugged camera out of the app (call on the UI thread).

        It leaves ``manager.camera_ids`` at once, so the UI drops it on the next frame; its
        recording is finished and the device is closed on a background thread (closing a lost
        GigE device can block)."""
        try:
            camera, worker = self.manager.detach_camera(camera_id)
        except KeyError:
            return
        logger.info("Removing %s %s (disconnected)", camera.model, camera.serial_number, extra=for_camera(camera_id))

        def finish() -> None:
            if worker is not None:
                worker.stop()
            if self.recording.is_recording(camera_id):
                self.recording.stop([camera_id])
            self.manager.release(camera, None)

        threading.Thread(target=finish, name=f"remove-{camera_id}", daemon=True).start()

    def start(self) -> None:
        self.reconnect.start()
        if self.discovery.enabled:
            self.discovery.start()

    def shutdown(self) -> None:
        """Order matters: stop discovery and reconnecting, finish recordings, then release cameras."""
        self.discovery.stop()
        self.reconnect.stop()
        self.recording.stop()
        self.manager.shutdown()
