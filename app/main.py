"""Entry point: python -m app.main [--config PATH] [--log-level LEVEL]"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import dearpygui.dearpygui as dpg

from app.services.configuration import load_config
from app.services.logging_service import setup_logging
from app.ui.main_window import APP_TITLE, build_main_window

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--log-level", default=None, help="Overrides logging.level in config")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args.config)
    log_cfg = config["logging"]
    log_file = setup_logging(Path(log_cfg["directory"]), args.log_level or log_cfg["level"])
    logger.info("Starting %s (log: %s)", APP_TITLE, log_file)

    dpg.create_context()
    try:
        dpg.create_viewport(title=APP_TITLE, width=1400, height=860, min_width=900, min_height=600)
        build_main_window(config)
        dpg.setup_dearpygui()
        dpg.show_viewport()
        dpg.start_dearpygui()
    finally:
        dpg.destroy_context()
        logger.info("Shut down")


if __name__ == "__main__":
    main()
