"""Entry point: python -m app.main [--config PATH] [--log-level LEVEL] [--simulators N]"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import dearpygui.dearpygui as dpg

from app.cameras.camera_manager import CameraManager
from app.cameras.simulator_camera import PATTERNS, SimulatorCamera, SimulatorConfig
from app.services.configuration import load_config
from app.services.logging_service import setup_logging
from app.ui.main_window import APP_TITLE, MainWindow

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--log-level", default=None, help="Overrides logging.level in config")
    parser.add_argument(
        "--simulators", type=int, default=4, help="Number of SimulatorCameras to add (default 4)"
    )
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


def main() -> None:
    args = parse_args()
    config = load_config(args.config)
    log_cfg = config["logging"]
    log_file = setup_logging(Path(log_cfg["directory"]), args.log_level or log_cfg["level"])
    logger.info("Starting %s (log: %s)", APP_TITLE, log_file)

    manager = CameraManager()
    add_simulators(manager, args.simulators)

    dpg.create_context()
    try:
        dpg.create_viewport(title=APP_TITLE, width=1400, height=860, min_width=900, min_height=600)
        window = MainWindow(config, manager)
        dpg.setup_dearpygui()
        dpg.show_viewport()
        while dpg.is_dearpygui_running():
            window.update()
            dpg.render_dearpygui_frame()
    finally:
        manager.shutdown()
        dpg.destroy_context()
        logger.info("Shut down")


if __name__ == "__main__":
    main()
