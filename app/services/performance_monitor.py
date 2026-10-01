"""Performance metrics: per-camera acquisition/recording figures and host CPU, RAM, NIC, disk.

``sample()`` is cheap to call every UI frame: host counters are re-read at most once per interval
and rates are computed from counter deltas.
"""

from __future__ import annotations

import ipaddress
import logging
import time
from dataclasses import dataclass, field

import psutil

from app.cameras.camera_manager import CameraManager
from app.models.camera_state import CameraState
from app.services.recording_service import RecordingService

logger = logging.getLogger(__name__)

MB = 1e6


@dataclass(frozen=True)
class CameraPerf:
    camera_id: str
    state: CameraState
    camera_fps: float = 0.0
    throughput_mb_s: float = 0.0
    frames_missed: int = 0
    timeouts: int = 0
    nic: str | None = None
    recording: bool = False
    recording_queue_depth: int = 0
    recording_dropped: int = 0


@dataclass(frozen=True)
class HostPerf:
    cpu_percent: float = 0.0  # whole machine
    process_cpu_percent: float = 0.0  # this app; can exceed 100 on multi-core
    process_memory_mb: float = 0.0
    memory_percent: float = 0.0  # whole machine
    nic_rx_mb_s: dict[str, float] = field(default_factory=dict)
    disk_write_mb_s: float = 0.0  # whole machine
    recording_write_mb_s: float = 0.0  # this app's recorders


@dataclass(frozen=True)
class PerformanceSample:
    host: HostPerf
    cameras: list[CameraPerf]


class PerformanceMonitor:
    def __init__(self, manager: CameraManager, recording: RecordingService | None = None, interval: float = 1.0) -> None:
        self._manager = manager
        self._recording = recording
        self._interval = interval
        self._process = psutil.Process()
        self._process.cpu_percent(None)  # prime: the first call always returns 0
        psutil.cpu_percent(None)
        self._last_time = time.monotonic()
        self._last_net = self._net_counters()
        self._last_disk = self._disk_write_bytes()
        self._last_recorded = 0
        self._host = HostPerf()
        self._nic_cache: dict[str, str | None] = {}

    def sample(self) -> PerformanceSample:
        now = time.monotonic()
        if now - self._last_time >= self._interval:
            self._host = self._sample_host(now)
        return PerformanceSample(self._host, [self._camera_perf(cid) for cid in self._manager.camera_ids])

    def camera_nic(self, ip: str | None) -> str | None:
        """Name of the host interface on the same subnet as ``ip`` (the NIC carrying that camera)."""
        if not ip:
            return None
        if ip not in self._nic_cache:
            self._nic_cache[ip] = _nic_for_ip(ip, psutil.net_if_addrs())
        return self._nic_cache[ip]

    def _sample_host(self, now: float) -> HostPerf:
        elapsed = now - self._last_time
        net = self._net_counters()
        disk = self._disk_write_bytes()
        recorded = self._recorded_bytes()
        nic_rates = {
            nic: (rx - self._last_net.get(nic, rx)) / elapsed / MB
            for nic, rx in net.items()
        }
        host = HostPerf(
            cpu_percent=psutil.cpu_percent(None),
            process_cpu_percent=self._process.cpu_percent(None),
            process_memory_mb=self._process.memory_info().rss / MB,
            memory_percent=psutil.virtual_memory().percent,
            nic_rx_mb_s={nic: rate for nic, rate in nic_rates.items() if rate >= 0},
            disk_write_mb_s=max(0.0, (disk - self._last_disk) / elapsed / MB) if disk is not None and self._last_disk is not None else 0.0,
            recording_write_mb_s=max(0.0, (recorded - self._last_recorded) / elapsed / MB),
        )
        self._last_time, self._last_net, self._last_disk, self._last_recorded = now, net, disk, recorded
        return host

    def _camera_perf(self, camera_id: str) -> CameraPerf:
        camera = self._manager.camera(camera_id)
        stats = self._manager.stats(camera_id)
        rec_status = self._recording.status() if self._recording is not None else None
        rec = rec_status.cameras.get(camera_id) if rec_status is not None and rec_status.active else None
        return CameraPerf(
            camera_id=camera_id,
            state=self._manager.state(camera_id),
            camera_fps=stats.measured_fps if stats else 0.0,
            throughput_mb_s=stats.throughput_mb_s if stats else 0.0,
            frames_missed=stats.frames_missed if stats else 0,
            timeouts=stats.timeouts if stats else 0,
            nic=self.camera_nic(camera.ip_address),
            recording=rec is not None,
            recording_queue_depth=rec.queue_depth if rec else 0,
            recording_dropped=(rec.queue_overflows + rec.frame_gaps) if rec else 0,
        )

    def _recorded_bytes(self) -> int:
        if self._recording is None:
            return 0
        status = self._recording.status()
        return status.bytes_written if status.active else self._last_recorded

    @staticmethod
    def _net_counters() -> dict[str, int]:
        try:
            return {nic: c.bytes_recv for nic, c in psutil.net_io_counters(pernic=True).items()}
        except Exception as exc:  # noqa: BLE001 - counters unavailable in some sandboxes
            logger.debug("NIC counters unavailable: %s", exc)
            return {}

    @staticmethod
    def _disk_write_bytes() -> int | None:
        try:
            counters = psutil.disk_io_counters()
            return counters.write_bytes if counters else None
        except Exception as exc:  # noqa: BLE001
            logger.debug("Disk counters unavailable: %s", exc)
            return None


def _nic_for_ip(ip: str, if_addrs: dict) -> str | None:
    try:
        target = ipaddress.IPv4Address(ip)
    except ValueError:
        return None
    for nic, addrs in if_addrs.items():
        for addr in addrs:
            if getattr(addr, "netmask", None) and addr.address.count(".") == 3:
                try:
                    if target in ipaddress.IPv4Network(f"{addr.address}/{addr.netmask}", strict=False):
                        return nic
                except ValueError:
                    continue
    return None
