"""Entry point: python -m app.main [--config PATH] [--log-level LEVEL] [--simulators N] [--no-arena]"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

import dearpygui.dearpygui as dpg

from app.cameras.arena_camera import ArenaCamera
from app.cameras.camera_device import CameraError
from app.cameras.camera_discovery import discover_arena_cameras
from app.cameras.camera_manager import CameraManager
from app.cameras.simulator_camera import PATTERNS, SimulatorCamera, SimulatorConfig
from app.services.configuration import load_config
from app.services.logging_service import setup_logging
from app.services.app_services import AppServices
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
        help="Number of SimulatorCameras to add (default: 4 if no Arena camera is found, else 0)",
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


def main() -> None:
    args = parse_args()
    config = load_config(args.config)
    log_cfg = config["logging"]
    log_file = setup_logging(Path(log_cfg["directory"]), args.log_level or log_cfg["level"])
    logger.info("Starting %s (log: %s)", APP_TITLE, log_file)

    manager = CameraManager()
    arena_count = 0 if args.no_arena else add_arena_cameras(manager)
    simulators = args.simulators if args.simulators is not None else (0 if arena_count else 4)
    if simulators and not arena_count and args.simulators is None:
        logger.info("No Arena cameras found; adding %d simulator cameras", simulators)
    add_simulators(manager, simulators)

    services = AppServices.create(config, args.config, manager)
    services.start()

    dpg.create_context()
    try:
        dpg.create_viewport(title=APP_TITLE, width=1400, height=860, min_width=900, min_height=600)
        window = MainWindow(services)
        dpg.setup_dearpygui()
        dpg.show_viewport()
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
