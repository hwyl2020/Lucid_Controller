"""Application configuration stored as JSON, merged over built-in defaults."""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_CONFIG: dict[str, Any] = {
    "application": {
        "theme": "dark",
        "accent": "Azure",  # UI accent colour: Azure, Indigo, Violet, Teal or Graphite
        "default_layout": "2x2",
    },
    "ui": {
        "stats_refresh_hz": 5,  # camera status / log panel refresh rate (not tied to camera FPS)
        "status_panel_open": True,
        "log_panel_open": False,
        # Pane sizes, changed by dragging the resize handles (View > Reset layout restores them).
        "sidebar_width": 392,
        "status_panel_height": None,  # None = fit to the number of cameras
        "log_panel_height": 180,
    },
    "recording": {
        "directory": "recordings",
        "mode": "raw",  # "raw" (lossless, full resolution) or "video" (half-resolution MP4)
        "queue_frames": 64,  # per camera; 64 x 12 MP BayerRG8 is ~780 MB of RAM
        "min_free_gb": 2.0,  # recording stops before the disk fills up
        # frames.csv (per-frame id + timestamp) and session.json (settings, counts) next to each
        # recording. Off: video recordings are just the video file. Raw always keeps frames.csv
        # (it is the index needed to read frames.raw back).
        "save_metadata": False,
    },
    "snapshots": {
        "directory": "snapshots",
    },
    "profiles": {
        "directory": "profiles",
    },
    "sessions": {
        "directory": "sessions",
    },
    "reconnect": {
        "enabled": True,
    },
    "diagnostics": {
        "directory": "diagnostics",
    },
    "logging": {
        "directory": "logs",
        "level": "INFO",
    },
    "cameras": {},
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_config(path: Path) -> dict[str, Any]:
    """Load config from ``path``. Missing or unreadable files fall back to defaults."""
    if not path.exists():
        logger.info("No config file at %s; using defaults", path)
        return copy.deepcopy(DEFAULT_CONFIG)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read config %s (%s); using defaults", path, exc)
        return copy.deepcopy(DEFAULT_CONFIG)
    if not isinstance(data, dict):
        logger.warning("Config %s is not a JSON object; using defaults", path)
        return copy.deepcopy(DEFAULT_CONFIG)
    return _deep_merge(DEFAULT_CONFIG, data)


def save_config(config: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
