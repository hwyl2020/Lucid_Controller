"""Where the app keeps its files (config.json, logs, recordings, snapshots, profiles, ...).

From source (``python -m app.main``) everything is relative to the current directory, as before.
As a packaged Windows app (PyInstaller, ``sys.frozen``) the data lives next to the .exe when that
folder is writable (portable use, e.g. from a USB drive), otherwise in
``Documents\\Apertix`` (e.g. when installed under Program Files).
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from app import APP_NAME

DATA_FOLDER_NAME = APP_NAME


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """Folder of the .exe when packaged, else the current directory."""
    return Path(sys.executable).resolve().parent if is_frozen() else Path.cwd()


def is_writable(folder: Path) -> bool:
    try:
        with tempfile.TemporaryFile(dir=folder):
            return True
    except OSError:
        return False


def data_dir() -> Path:
    if not is_frozen():
        return Path.cwd()
    portable = app_dir()
    if is_writable(portable):
        return portable
    documents = Path(os.environ.get("USERPROFILE", Path.home())) / "Documents" / DATA_FOLDER_NAME
    documents.mkdir(parents=True, exist_ok=True)
    return documents


def use_data_dir() -> Path:
    """Make the data folder the working directory, so the relative paths in the config resolve there."""
    folder = data_dir()
    os.chdir(folder)
    return folder


def resource(name: str) -> Path:
    """A file shipped in app/resources (bundled at the same relative place in the packaged app)."""
    return Path(__file__).resolve().parent / "resources" / name
