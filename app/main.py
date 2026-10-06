"""Entry point: python -m app.main [--config PATH] [--log-level LEVEL] [--simulators N] [--no-arena]

Also the entry point of the packaged Windows app (``Apertix.exe``, see installer/):
there the data folder comes from ``app.paths`` and no simulator cameras are added unless asked for.
"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

import dearpygui.dearpygui as dpg

from app import paths
from app.cameras import arena_sdk
from app.cameras.arena_camera import ArenaCamera
from app.cameras.camera_device import CameraError
from app.cameras.camera_discovery import discover_arena_cameras
from app.cameras.camera_manager import CameraManager
from app.cameras.simulator_camera import PATTERNS, SimulatorCamera, SimulatorConfig
from app.services.configuration import load_config
from app.services.logging_service import setup_logging
from app.services import log_buffer
from app.services.app_services import AppServices
from app.services.discovery_service import arena_scan
from app.ui import dialogs
from app.ui.main_window import APP_TITLE, MainWindow

logger = logging.getLogger(__name__)

UI_MAX_FPS = 60


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--log-level", default=None, help="Overrides logging.level in config")
    parser.add_argument(
        "--simulators",
        type=int,
        default=None,
        help="Number of SimulatorCameras to add (default: 4 if no Arena camera is found, else 0; "
             "0 in the packaged app)",
    )
    parser.add_argument("--no-arena", action="store_true", help="Skip Arena SDK camera discovery")
    return parser.parse_args()


def add_simulators(manager: CameraManager, count: int) -> None:
    for i in range(count):
        manager.add_camera(
            SimulatorCamera(
                SimulatorConfig(
                    camera_id=f"SIM-{i + 1:02d}",
                    serial_number=f"SIM{i + 1:08d}",
                    pattern=PATTERNS[i % len(PATTERNS)],
                    pixel_format="RGB8" if i % 2 else "Mono8",
                )
            )
        )


def add_arena_cameras(manager: CameraManager) -> int:
    try:
        infos = discover_arena_cameras()
    except CameraError as exc:
        logger.error("%s", exc)
        return 0
    for info in infos:
        manager.add_camera(ArenaCamera(info))
    return len(infos)


SDK_MISSING_TEXT = (
    "The LUCID Arena SDK is not installed on this PC, so cameras cannot be found.\n\n"
    "Install the Arena SDK for Windows (64-bit) from LUCID Vision Labs (thinklucid.com > Downloads), "
    "then restart Apertix.\n\nDetails: {error}"
)


def check_arena_sdk() -> str | None:
    """None if the Arena SDK loads, else the reason it does not."""
    try:
        arena_sdk.load()
    except arena_sdk.ArenaSdkUnavailable as exc:
        return str(exc)
    return None


def main() -> None:
    if paths.is_frozen():
        paths.use_data_dir()
    args = parse_args()
    config = load_config(args.config)
    log_cfg = config["logging"]
    log_file = setup_logging(Path(log_cfg["directory"]), args.log_level or log_cfg["level"])
    logs = log_buffer.install()
    logger.info("Starting %s (log: %s, data folder: %s)", APP_TITLE, log_file, Path.cwd())

    manager = CameraManager()
    sdk_error = None if args.no_arena else check_arena_sdk()
    if sdk_error:
        logger.error("%s", sdk_error)
    arena_count = 0 if args.no_arena or sdk_error else add_arena_cameras(manager)
    default_simulators = 0 if arena_count or paths.is_frozen() else 4
    simulators = args.simulators if args.simulators is not None else default_simulators
    if simulators and not arena_count and args.simulators is None:
        logger.info("No Arena cameras found; adding %d simulator cameras", simulators)
    add_simulators(manager, simulators)

    # Hot-plug: cameras connected later are found by the discovery service (no restart needed).
    discover = None if args.no_arena or sdk_error else arena_scan
    services = AppServices.create(config, args.config, manager, logs, discover=discover)
    services.start()

    dpg.create_context()
    try:
        icon = paths.resource("app.ico")
        icons = {"small_icon": str(icon), "large_icon": str(icon)} if icon.exists() else {}
        dpg.create_viewport(title=APP_TITLE, width=1400, height=860, min_width=900, min_height=600, **icons)
        window = MainWindow(services)
        dpg.setup_dearpygui()
        dpg.show_viewport()
        if sdk_error and paths.is_frozen():
            dialogs.message("Arena SDK not found", SDK_MISSING_TEXT.format(error=sdk_error))
        frame_period = 1.0 / UI_MAX_FPS
        while dpg.is_dearpygui_running():
            started = time.perf_counter()
            window.update()
            dpg.render_dearpygui_frame()
            # Cap the UI rate: spare CPU matters for GigE packet handling at high bandwidth.
            remaining = frame_period - (time.perf_counter() - started)
            if remaining > 0:
                time.sleep(remaining)
    finally:
        services.shutdown()  # stop reconnects, finalise recordings, release cameras
        dpg.destroy_context()
        logger.info("Shut down")


if __name__ == "__main__":
    main()
