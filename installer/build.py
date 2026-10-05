"""Build the portable Windows app: dist/LUCID Camera Studio/ plus a zip of it.

    .venv\\Scripts\\python -m pip install -r requirements-build.txt
    .venv\\Scripts\\python -m installer.build

The result runs on any 64-bit Windows 10/11 PC without Python; that PC needs the LUCID Arena SDK
installed (see installer/README_FIRST.txt, which is copied into the folder).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from app import __version__
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
      StringStruct('CompanyName', ''),
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


def main() -> None:
    parts = [int(p) for p in __version__.split(".")][:4]
    v4 = tuple(parts + [0] * (4 - len(parts)))
    BUILD.mkdir(exist_ok=True)
    (BUILD / "version_info.txt").write_text(
        VERSION_INFO.format(v4=v4, title=APP_TITLE, version=__version__), encoding="utf-8")

    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", str(DIST), "--workpath", str(BUILD / "pyinstaller"),
                    str(ROOT / "installer" / "lucid_camera_studio.spec")], check=True, cwd=ROOT)

    readme = (ROOT / "installer" / "README_FIRST.txt").read_text(encoding="utf-8")
    (APP_DIR / "README_FIRST.txt").write_text(readme.replace("{version}", __version__), encoding="utf-8")

    archive = shutil.make_archive(str(DIST / f"LUCID_Camera_Studio_{__version__}_win64"), "zip", DIST, APP_TITLE)
    size_mb = sum(f.stat().st_size for f in APP_DIR.rglob("*") if f.is_file()) / 1e6
    print(f"\nBuilt {APP_DIR} ({size_mb:.0f} MB)\nZip:  {archive}")


if __name__ == "__main__":
    main()
