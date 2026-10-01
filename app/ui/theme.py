"""Dear PyGui themes. Dark is the default."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import dearpygui.dearpygui as dpg

from app.models.camera_state import CameraState

logger = logging.getLogger(__name__)

TEXT_DIM = (142, 142, 147)

STATE_COLORS = {
    CameraState.DISCONNECTED: (142, 142, 147),
    CameraState.CONNECTED: (10, 132, 255),
    CameraState.ACQUIRING: (48, 209, 88),
    CameraState.ERROR: (255, 69, 58),
}

_PALETTES = {
    "dark": {
        "window_bg": (28, 28, 30),
        "child_bg": (36, 36, 38),
        "frame_bg": (44, 44, 46),
        "frame_hover": (58, 58, 60),
        "text": (235, 235, 245),
        "text_dim": (142, 142, 147),
        "border": (56, 56, 58),
        "accent": (10, 132, 255),
        "accent_hover": (64, 156, 255),
    },
    "light": {
        "window_bg": (242, 242, 247),
        "child_bg": (255, 255, 255),
        "frame_bg": (229, 229, 234),
        "frame_hover": (209, 209, 214),
        "text": (28, 28, 30),
        "text_dim": (108, 108, 112),
        "border": (209, 209, 214),
        "accent": (0, 122, 255),
        "accent_hover": (40, 140, 255),
    },
}


# First existing font wins; Dear PyGui's built-in font lacks glyphs such as the state dot (U+25CF).
FONT_CANDIDATES = (
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "segoeui.ttf",
    Path("/System/Library/Fonts/SFNS.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
)


def load_font(size: int = 16) -> None:
    """Bind the first available UI font; keep the built-in font if none is found."""
    path = next((p for p in FONT_CANDIDATES if p.exists()), None)
    if path is None:
        logger.warning("No UI font found; using Dear PyGui default (state dots may render as '?')")
        return
    # Dear PyGui 2.x loads glyph ranges automatically (font range hints are deprecated no-ops).
    with dpg.font_registry():
        font = dpg.add_font(str(path), size)
    dpg.bind_font(font)


def create_theme(name: str = "dark") -> int:
    p = _PALETTES.get(name, _PALETTES["dark"])
    with dpg.theme() as theme:
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg, p["window_bg"])
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, p["child_bg"])
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg, p["frame_bg"])
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, p["frame_hover"])
            dpg.add_theme_color(dpg.mvThemeCol_Text, p["text"])
            dpg.add_theme_color(dpg.mvThemeCol_TextDisabled, p["text_dim"])
            dpg.add_theme_color(dpg.mvThemeCol_Border, p["border"])
            dpg.add_theme_color(dpg.mvThemeCol_Button, p["frame_bg"])
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, p["frame_hover"])
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, p["accent"])
            dpg.add_theme_color(dpg.mvThemeCol_Header, p["accent"])
            dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, p["accent_hover"])
            dpg.add_theme_color(dpg.mvThemeCol_CheckMark, p["accent"])
            dpg.add_theme_color(dpg.mvThemeCol_SliderGrab, p["accent"])
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 0)
            dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 8)
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 6)
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 12, 12)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 8, 8)
            dpg.add_theme_style(dpg.mvStyleVar_WindowBorderSize, 0)
        # Disabled widgets must look disabled; by default they are indistinguishable.
        for item_type in (dpg.mvButton, dpg.mvInputFloat, dpg.mvInputInt, dpg.mvSliderFloat, dpg.mvCombo):
            with dpg.theme_component(item_type, enabled_state=False):
                dpg.add_theme_color(dpg.mvThemeCol_Text, p["text_dim"])
                dpg.add_theme_color(dpg.mvThemeCol_Button, p["window_bg"])
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, p["window_bg"])
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, p["window_bg"])
                dpg.add_theme_color(dpg.mvThemeCol_FrameBg, p["window_bg"])
                dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, p["window_bg"])
    return theme
