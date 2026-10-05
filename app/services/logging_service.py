"""Root logger setup: console plus a rotating file in the log directory."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.camera_log import CameraFieldFilter

# [camera_id] is the camera a record concerns ("-" for application-wide records); see app/camera_log.py
LOG_FORMAT = "%(asctime)s %(levelname)-8s [%(camera_id)s] [%(threadName)s] %(name)s: %(message)s"


def setup_logging(log_dir: Path, level: str = "INFO") -> Path:
    """Configure the root logger and return the log file path."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "lucid_camera_studio.log"

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(LOG_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(CameraFieldFilter())
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(CameraFieldFilter())
    root.addHandler(file_handler)

    return log_file
