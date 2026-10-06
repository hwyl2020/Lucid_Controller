# PyInstaller spec: one-folder Windows build of VisionX.
# Build with:  .venv\Scripts\python -m installer.build
#
# arena_api (LUCID's Python wrapper) is bundled; it loads the ArenaC DLLs from the Arena SDK that
# must be installed on the target PC (found through the SDK's registry key), so the SDK's DLLs and
# GigE filter driver are not copied here.
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent
ICON = str(ROOT / "app" / "resources" / "app.ico")

a = Analysis(
    [str(ROOT / "installer" / "launcher.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "app" / "resources"), "app/resources")],
    hiddenimports=collect_submodules("arena_api") + collect_submodules("app"),
    excludes=["matplotlib", "pytest", "IPython", "PIL", "PyInstaller"],  # tkinter: folder dialog
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VisionX",
    icon=ICON,
    console=False,  # windowed app; everything is logged to logs/visionx.log
    upx=False,
    version=str(ROOT / "build" / "version_info.txt"),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="VisionX")
