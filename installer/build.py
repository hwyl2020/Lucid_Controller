"""Build the Windows app: dist/VisionX/ (portable folder), a zip of it and, when Inno Setup 6 is
installed, dist/VisionX_Setup_<version>.exe (installer/visionx.iss).

    .venv\\Scripts\\python -m pip install -r requirements-build.txt
    winget install --id JRSoftware.InnoSetup -e        (once, for the Setup.exe)
    .venv\\Scripts\\python -m installer.build [--no-installer]

The result runs on any 64-bit Windows 10/11 PC without Python; that PC needs the LUCID Arena SDK
installed (see installer/README_FIRST.txt, which is copied into the folder).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from app import APP_PUBLISHER, __version__
from app.main import APP_TITLE

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
DIST = ROOT / "dist"
APP_DIR = DIST / APP_TITLE

VERSION_INFO = """VSVersionInfo(
  ffi=FixedFileInfo(filevers={v4}, prodvers={v4}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1,
                    subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', '{publisher}'),
      StringStruct('FileDescription', '{title}'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', '{title}'),
      StringStruct('OriginalFilename', '{title}.exe'),
      StringStruct('ProductName', '{title}'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def find_iscc() -> Path | None:
    """Inno Setup's command-line compiler (machine-wide or per-user install), or None."""
    found = shutil.which("ISCC")
    if found:
        return Path(found)
    roots = [os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")]
    for root in filter(None, roots):
        candidate = Path(root) / "Inno Setup 6" / "ISCC.exe"
        if candidate.exists():
            return candidate
    return None


def build_installer() -> Path | None:
    iscc = find_iscc()
    if iscc is None:
        print("\nInno Setup 6 not found: skipped Setup.exe (winget install --id JRSoftware.InnoSetup -e)")
        return None
    subprocess.run([str(iscc), "/Q", f"/DAppVersion={__version__}", f"/DAppPublisher={APP_PUBLISHER}",
                    str(ROOT / "installer" / "visionx.iss")], check=True, cwd=ROOT / "installer")
    return DIST / f"{APP_TITLE}_Setup_{__version__}.exe"


def main() -> None:
    parts = [int(p) for p in __version__.split(".")][:4]
    v4 = tuple(parts + [0] * (4 - len(parts)))
    BUILD.mkdir(exist_ok=True)
    (BUILD / "version_info.txt").write_text(
        VERSION_INFO.format(v4=v4, title=APP_TITLE, version=__version__, publisher=APP_PUBLISHER), encoding="utf-8")

    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", str(DIST), "--workpath", str(BUILD / "pyinstaller"),
                    str(ROOT / "installer" / "visionx.spec")], check=True, cwd=ROOT)

    readme = (ROOT / "installer" / "README_FIRST.txt").read_text(encoding="utf-8")
    (APP_DIR / "README_FIRST.txt").write_text(readme.replace("{version}", __version__), encoding="utf-8")

    archive = shutil.make_archive(str(DIST / f"VisionX_{__version__}_win64"), "zip", DIST, APP_TITLE)
    size_mb = sum(f.stat().st_size for f in APP_DIR.rglob("*") if f.is_file()) / 1e6
    print(f"\nBuilt {APP_DIR} ({size_mb:.0f} MB)\nZip:  {archive}")
    if "--no-installer" not in sys.argv:
        setup = build_installer()
        if setup is not None:
            print(f"Setup: {setup} ({setup.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
