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
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Apertix.exe"))
    assert paths.data_dir() == tmp_path


def test_packaged_app_in_a_protected_folder_uses_documents(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Program Files" / "app.exe"))
    monkeypatch.setattr(paths, "is_writable", lambda folder: False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "user"))
    folder = paths.data_dir()
    assert folder == tmp_path / "user" / "Documents" / "Apertix"
    assert folder.is_dir()


def test_icon_resource_is_shipped():
    assert paths.resource("app.ico").stat().st_size > 1000
