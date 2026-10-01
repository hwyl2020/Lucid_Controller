"""Dear PyGui themes and fonts. Dark is the default.

Design intent (macOS-inspired, not a copy): neutral greys, one accent colour used sparingly
(focus/selection/primary actions), status colours only for status, rounded but quiet surfaces,
Segoe UI for text, a semibold weight for titles and a monospace face for logs and figures.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import dearpygui.dearpygui as dpg

from app.models.camera_state import CameraState

logger = logging.getLogger(__name__)

TEXT_DIM = (142, 142, 147)
WARNING_COLOR = (255, 159, 10)
THEME_TEXT = (-255, 0, 0, 255)  # Dear PyGui sentinel: use the theme's text colour

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
        "frame_bg": (48, 48, 51),
        "frame_hover": (60, 60, 64),
        "text": (235, 235, 245),
        "text_dim": (142, 142, 147),
        "border": (58, 58, 61),
        "accent": (10, 132, 255),
        "accent_hover": (64, 156, 255),
        "selection": (10, 132, 255, 70),
        "selection_hover": (10, 132, 255, 40),
        "section": (44, 44, 47),
        "section_hover": (52, 52, 56),
        "bar": (22, 22, 24),
        "table_header": (44, 44, 47),
        "row_alt": (40, 40, 42),
        "popup": (40, 40, 43),
    },
    "light": {
        "window_bg": (242, 242, 247),
        "child_bg": (255, 255, 255),
        "frame_bg": (232, 232, 237),
        "frame_hover": (218, 218, 224),
        "text": (28, 28, 30),
        "text_dim": (108, 108, 112),
        "border": (214, 214, 219),
        "accent": (0, 122, 255),
        "accent_hover": (40, 140, 255),
        "selection": (0, 122, 255, 45),
        "selection_hover": (0, 122, 255, 25),
        "section": (236, 236, 240),
        "section_hover": (226, 226, 231),
        "bar": (232, 232, 237),
        "table_header": (236, 236, 240),
        "row_alt": (247, 247, 250),
        "popup": (255, 255, 255),
    },
}


# First existing file wins for each role. Dear PyGui's built-in font lacks glyphs such as the
# state dot (U+25CF), so a system font is required for the intended look.
_FONT_DIR = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
FONT_CANDIDATES = {
    "body": (_FONT_DIR / "segoeui.ttf", Path("/System/Library/Fonts/SFNS.ttf"),
             Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")),
    "heading": (_FONT_DIR / "seguisb.ttf", _FONT_DIR / "segoeuib.ttf",
                Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")),
    "mono": (_FONT_DIR / "consola.ttf", Path("/System/Library/Fonts/SFNSMono.ttf"),
             Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")),
}
FONT_SIZES = {"body": 16, "heading": 16, "mono": 14}


@dataclass(frozen=True)
class Fonts:
    body: int | str | None = None
    heading: int | str | None = None
    mono: int | str | None = None


_fonts = Fonts()


def load_fonts() -> Fonts:
    """Load body/heading/mono fonts (missing ones fall back to the body/default font)."""
    global _fonts
    loaded: dict[str, int | str | None] = {}
    with dpg.font_registry():
        for role, candidates in FONT_CANDIDATES.items():
            path = next((p for p in candidates if p.exists()), None)
            loaded[role] = dpg.add_font(str(path), FONT_SIZES[role]) if path else None
    if loaded["body"] is None:
        logger.warning("No UI font found; using Dear PyGui default (state dots may render as '?')")
    else:
        dpg.bind_font(loaded["body"])
    _fonts = Fonts(loaded["body"], loaded["heading"] or loaded["body"], loaded["mono"] or loaded["body"])
    return _fonts


def load_font(size: int = 16) -> None:
    """Backwards-compatible alias used by older callers."""
    load_fonts()


def fonts() -> Fonts:
    return _fonts


def use_font(item: int | str, role: str) -> None:
    """Apply the heading/mono font to an item if fonts are loaded."""
    font = getattr(_fonts, role)
    if font is not None:
        dpg.bind_item_font(item, font)


def compact_table_theme() -> int:
    """Dense rows for read-only tables (status cards, logs, sidebar details)."""
    with dpg.theme() as theme:
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_style(dpg.mvStyleVar_CellPadding, 6, 1)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 8, 2)
    return theme


def plain_button_theme() -> int:
    """Borderless, background-less button (disclosure arrows)."""
    with dpg.theme() as theme:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (0, 0, 0, 0))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (128, 128, 128, 40))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (128, 128, 128, 70))
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 2, 2)
    return theme


def segment_selected_theme(name: str = "dark") -> int:
    """Selected segment of a segmented control: quiet accent tint."""
    p = _PALETTES.get(name, _PALETTES["dark"])
    with dpg.theme() as theme:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, p["selection"])
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, p["selection"])
            dpg.add_theme_color(dpg.mvThemeCol_Text, p["accent"])
    return theme


def square_window_theme() -> int:
    """For the primary window: it fills the viewport, so rounded corners would show gaps."""
    with dpg.theme() as theme:
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 0)
    return theme


def create_theme(name: str = "dark") -> int:
    p = _PALETTES.get(name, _PALETTES["dark"])
    with dpg.theme() as theme:
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg, p["window_bg"])
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, p["child_bg"])
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg, p["frame_bg"])
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, p["frame_hover"])
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive, p["frame_hover"])
            dpg.add_theme_color(dpg.mvThemeCol_Text, p["text"])
            dpg.add_theme_color(dpg.mvThemeCol_TextDisabled, p["text_dim"])
            dpg.add_theme_color(dpg.mvThemeCol_Border, p["border"])
            dpg.add_theme_color(dpg.mvThemeCol_Button, p["frame_bg"])
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, p["frame_hover"])
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, p["accent"])
            # Selection is a quiet accent tint, not a solid blue bar.
            dpg.add_theme_color(dpg.mvThemeCol_Header, p["selection"])
            dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, p["selection_hover"])
            dpg.add_theme_color(dpg.mvThemeCol_HeaderActive, p["selection"])
            dpg.add_theme_color(dpg.mvThemeCol_CheckMark, p["accent"])
            dpg.add_theme_color(dpg.mvThemeCol_SliderGrab, p["accent"])
            dpg.add_theme_color(dpg.mvThemeCol_SliderGrabActive, p["accent_hover"])
            dpg.add_theme_color(dpg.mvThemeCol_TextSelectedBg, p["selection"])
            # Bars, tables and popups: otherwise they keep Dear PyGui's dark defaults in light mode.
            dpg.add_theme_color(dpg.mvThemeCol_MenuBarBg, p["bar"])
            dpg.add_theme_color(dpg.mvThemeCol_TitleBg, p["bar"])
            dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive, p["bar"])
            dpg.add_theme_color(dpg.mvThemeCol_TitleBgCollapsed, p["bar"])
            dpg.add_theme_color(dpg.mvThemeCol_TableHeaderBg, p["table_header"])
            dpg.add_theme_color(dpg.mvThemeCol_TableRowBgAlt, p["row_alt"])
            dpg.add_theme_color(dpg.mvThemeCol_TableBorderStrong, p["border"])
            dpg.add_theme_color(dpg.mvThemeCol_TableBorderLight, p["border"])
            dpg.add_theme_color(dpg.mvThemeCol_PopupBg, p["popup"])
            dpg.add_theme_color(dpg.mvThemeCol_Separator, p["border"])
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarBg, (0, 0, 0, 0))
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrab, p["frame_hover"])
            dpg.add_theme_color(dpg.mvThemeCol_ResizeGrip, (0, 0, 0, 0))
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 10)
            dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 10)
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 6)
            dpg.add_theme_style(dpg.mvStyleVar_PopupRounding, 8)
            dpg.add_theme_style(dpg.mvStyleVar_GrabRounding, 6)
            dpg.add_theme_style(dpg.mvStyleVar_ScrollbarRounding, 6)
            dpg.add_theme_style(dpg.mvStyleVar_ScrollbarSize, 10)
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 12, 10)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 8, 5)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 8, 6)
            dpg.add_theme_style(dpg.mvStyleVar_ItemInnerSpacing, 6, 4)
            dpg.add_theme_style(dpg.mvStyleVar_CellPadding, 8, 3)
            dpg.add_theme_style(dpg.mvStyleVar_IndentSpacing, 18)
            dpg.add_theme_style(dpg.mvStyleVar_WindowBorderSize, 0)
            dpg.add_theme_style(dpg.mvStyleVar_ChildBorderSize, 1)
            dpg.add_theme_style(dpg.mvStyleVar_PopupBorderSize, 1)
        # Section headers (Camera Status, Logs): neutral, not accent-coloured.
        with dpg.theme_component(dpg.mvCollapsingHeader):
            dpg.add_theme_color(dpg.mvThemeCol_Header, p["section"])
            dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, p["section_hover"])
            dpg.add_theme_color(dpg.mvThemeCol_HeaderActive, p["section_hover"])
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
