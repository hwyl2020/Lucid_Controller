"""Diagnostics bundle: one zip with everything needed to investigate a problem remotely.

Contents: system.json (OS, Python, package versions, CPU/RAM/disk), network.json (host interfaces with
MTU/speed), cameras.json (identity, state, last error, stats, settings), config.json, and the logs.
"""

from __future__ import annotations

import dataclasses
import importlib.metadata
import json
import logging
import os
import platform
import shutil
import sys
import time
import zipfile
from pathlib import Path

import psutil

from app import __version__
from app.cameras import arena_sdk
from app.cameras.camera_manager import CameraManager
from app.services.recording_service import camera_metadata

logger = logging.getLogger(__name__)

PACKAGES = ("dearpygui", "numpy", "opencv-python", "psutil", "arena_api")


def export_diagnostics(output_dir: Path, manager: CameraManager, config: dict, log_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / time.strftime("diagnostics_%Y%m%d_%H%M%S.zip")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("system.json", _json(_system_info(config)))
        bundle.writestr("network.json", _json(_network_info()))
        bundle.writestr("cameras.json", _json(_camera_info(manager)))
        bundle.writestr("config.json", _json(config))
        if log_dir.exists():
            for log_file in sorted(log_dir.glob("*.log*")):
                bundle.write(log_file, f"logs/{log_file.name}")
    logger.info("Diagnostics exported to %s", path)
    return path


def _system_info(config: dict) -> dict:
    versions = {}
    for package in PACKAGES:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    memory = psutil.virtual_memory()
    recordings = Path(config.get("recording", {}).get("directory", "."))
    try:
        disk = shutil.disk_usage(recordings if recordings.exists() else Path.cwd())
        disk_info = {"total_gb": round(disk.total / 1e9, 1), "free_gb": round(disk.free / 1e9, 1)}
    except OSError:
        disk_info = None
    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "application_version": __version__,
        "platform": platform.platform(),
        "python": sys.version,
        "executable": sys.executable,
        "cwd": os.getcwd(),
        "packages": versions,
        "cpu": {"logical": psutil.cpu_count(), "physical": psutil.cpu_count(logical=False), "percent": psutil.cpu_percent(0.2)},
        "memory_gb": {"total": round(memory.total / 1e9, 1), "available": round(memory.available / 1e9, 1)},
        "recording_disk": disk_info,
    }


def _network_info() -> dict:
    stats = psutil.net_if_stats()
    interfaces = {}
    for nic, addrs in psutil.net_if_addrs().items():
        st = stats.get(nic)
        interfaces[nic] = {
            "up": st.isup if st else None,
            "speed_mbps": st.speed if st else None,
            "mtu": st.mtu if st else None,
            "addresses": [
                {"family": str(a.family), "address": a.address, "netmask": a.netmask} for a in addrs
            ],
        }
    info: dict = {"interfaces": interfaces}
    if arena_sdk._sdk is not None:  # only if already loaded; don't open the SDK just for this
        try:
            info["arena_interfaces"] = arena_sdk._sdk.system.interface_infos
        except Exception as exc:  # noqa: BLE001
            info["arena_interfaces"] = f"unavailable: {exc}"
    return info


def _camera_info(manager: CameraManager) -> list[dict]:
    cameras = []
    for camera_id in manager.camera_ids:
        camera = manager.camera(camera_id)
        stats = manager.stats(camera_id)
        entry = {
            "type": type(camera).__name__,
            "state": manager.state(camera_id).value,
            "last_error": manager.last_error(camera_id),
            "acquisition_stats": dataclasses.asdict(stats) if stats else None,
            "settings": camera_metadata(camera) if camera.connected else None,
        }
        info = getattr(camera, "info", None)
        if dataclasses.is_dataclass(info):
            entry["device_info"] = dataclasses.asdict(info)
        for counter in ("missed_frames", "incomplete_frames"):
            if hasattr(camera, counter):
                entry[counter] = getattr(camera, counter)
        cameras.append({"camera_id": camera_id, "model": camera.model, "serial_number": camera.serial_number, **entry})
    return cameras


def _json(data) -> str:
    return json.dumps(data, indent=2, default=str)
