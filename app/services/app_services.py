"""Wiring of the application services the UI depends on."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.cameras.camera_manager import CameraManager
from app.services.camera_control_service import CameraControlService
from app.services.camera_status_service import CameraStatusService
from app.services.feature_service import FeatureService
from app.services.log_buffer import LogBuffer
from app.services.performance_monitor import PerformanceMonitor
from app.services.profile_service import ProfileService, SettingsApplier
from app.services.reconnect_service import ReconnectService
from app.services.recording_service import RecordingService
from app.services.session_manager import SessionManager


@dataclass
class AppServices:
    config: dict
    config_path: Path
    log_dir: Path
    manager: CameraManager
    controls: CameraControlService
    statuses: CameraStatusService
    features: FeatureService
    logs: LogBuffer
    recording: RecordingService
    reconnect: ReconnectService
    performance: PerformanceMonitor
    profiles: ProfileService
    sessions: SessionManager

    @classmethod
    def create(
        cls, config: dict, config_path: Path, manager: CameraManager, logs: LogBuffer | None = None
    ) -> AppServices:
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
            features=FeatureService(manager),
            logs=logs if logs is not None else LogBuffer(),
            recording=recording,
            reconnect=reconnect,
            performance=PerformanceMonitor(manager, recording, statuses=statuses),
            profiles=ProfileService(manager, applier, Path(config["profiles"]["directory"])),
            sessions=SessionManager(manager, applier, Path(config["sessions"]["directory"])),
        )

    def start(self) -> None:
        self.reconnect.start()

    def shutdown(self) -> None:
        """Order matters: stop reconnecting, finish recordings, then release cameras."""
        self.reconnect.stop()
        self.recording.stop()
        self.manager.shutdown()
