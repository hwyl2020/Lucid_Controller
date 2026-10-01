"""Main window shell: header, camera sidebar, multiview area, status bar.

Milestone 1 only lays out the regions; sidebar and multiview content come in Milestone 2.
"""

from __future__ import annotations

import dearpygui.dearpygui as dpg

from app.ui.theme import create_theme

APP_TITLE = "LUCID Camera Studio"
SIDEBAR_WIDTH = 240
HEADER_HEIGHT = 40
STATUS_HEIGHT = 32


def build_main_window(config: dict) -> None:
    app_cfg = config["application"]

    with dpg.window(tag="main_window"):
        with dpg.child_window(height=HEADER_HEIGHT, border=False, no_scrollbar=True):
            with dpg.group(horizontal=True):
                dpg.add_text(APP_TITLE)
                dpg.add_spacer(width=24)
                dpg.add_text("No cameras connected", tag="header_status", color=(142, 142, 147))

        with dpg.group(horizontal=True):
            with dpg.child_window(tag="camera_sidebar", width=SIDEBAR_WIDTH, height=-STATUS_HEIGHT - 8):
                dpg.add_text("CAMERAS", color=(142, 142, 147))
                dpg.add_separator()
                dpg.add_text("No cameras discovered", color=(142, 142, 147))

            with dpg.child_window(tag="multiview", width=-1, height=-STATUS_HEIGHT - 8):
                dpg.add_text(f"MULTIVIEW  ({app_cfg['default_layout']})", color=(142, 142, 147))

        with dpg.child_window(height=STATUS_HEIGHT, border=False, no_scrollbar=True):
            dpg.add_text("0 Cameras | Recording OFF", tag="status_bar_text")

    dpg.bind_theme(create_theme(app_cfg["theme"]))
    dpg.set_primary_window("main_window", True)
