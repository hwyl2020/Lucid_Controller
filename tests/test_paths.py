"""Data folder of the packaged app: next to the .exe if writable (portable), else Documents."""

import sys

from app import paths


def test_from_source_uses_the_current_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert not paths.is_frozen()
    assert paths.data_dir() == tmp_path


def test_packaged_app_is_portable_when_its_folder_is_writable(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "VisionX.exe"))
    assert paths.data_dir() == tmp_path


def test_packaged_app_in_a_protected_folder_uses_documents(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Program Files" / "app.exe"))
    monkeypatch.setattr(paths, "is_writable", lambda folder: False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "user"))
    folder = paths.data_dir()
    assert folder == tmp_path / "user" / "Documents" / "VisionX"
    assert folder.is_dir()


def test_icon_resource_is_shipped():
    assert paths.resource("app.ico").stat().st_size > 1000


def test_unwritable_folder_is_detected_with_a_single_attempt(tmp_path, monkeypatch):
    """Regression: tempfile.TemporaryFile looped ~forever on a denied folder (Program Files)."""
    attempts = []

    def denied(path, flags, *args):
        attempts.append(path)
        raise PermissionError(13, "Access is denied", str(path))

    monkeypatch.setattr(paths.os, "open", denied)
    assert paths.is_writable(tmp_path) is False
    assert len(attempts) == 1


def test_writable_probe_leaves_no_file(tmp_path):
    assert paths.is_writable(tmp_path) is True
    assert list(tmp_path.iterdir()) == []
