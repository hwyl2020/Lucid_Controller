"""Tag log records with the camera they concern, so the log viewer can show and filter by camera.

Per-camera objects log through ``camera_logger(logger, camera_id)``; functions that receive a
camera id pass ``extra=for_camera(camera_id)``. Records without a camera are application-wide
("System"). The tag is the record attribute ``camera_id``.
"""

from __future__ import annotations

import logging

CAMERA_ATTR = "camera_id"
NO_CAMERA = "-"  # shown in the log file for application-wide records


def for_camera(camera_id: str) -> dict:
    """``extra=`` mapping that tags a single log call with ``camera_id``."""
    return {CAMERA_ATTR: camera_id}


class CameraLoggerAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        kwargs["extra"] = {**kwargs.get("extra", {}), CAMERA_ATTR: self.extra[CAMERA_ATTR]}
        return msg, kwargs


def camera_logger(logger: logging.Logger, camera_id: str) -> CameraLoggerAdapter:
    return CameraLoggerAdapter(logger, {CAMERA_ATTR: camera_id})


def record_camera(record: logging.LogRecord) -> str | None:
    value = getattr(record, CAMERA_ATTR, None)
    return None if value in (None, NO_CAMERA) else str(value)


class CameraFieldFilter(logging.Filter):
    """Gives every record a ``camera_id`` attribute so formatters can use ``%(camera_id)s``."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, CAMERA_ATTR):
            setattr(record, CAMERA_ATTR, NO_CAMERA)
        return True
