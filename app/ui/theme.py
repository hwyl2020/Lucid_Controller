"""Design system: colour tokens, accents, fonts and reusable component themes.

The look is the app's own (platform-neutral) "camera workstation" design: cool graphite surfaces
in dark mode, clean paper-white cards in light mode, one accent colour used for focus, selection
and primary actions, and status colours reserved for status (live/success, warning, error/record).

How colours work
----------------
* ``PALETTES`` holds the neutral tokens per theme, ``ACCENTS`` the selectable accent colours
  (each with a dark and a light variant), ``_STATUS`` the status colours per theme.
* ``COLORS`` and ``STATE_COLORS`` are the *current* resolved values. They are mutated in place by
  ``apply()``, so modules that read ``COLORS["error"]`` at use time always get the live value.
* Component themes come from ``role(name)``: created once (lazily), their colours registered by
  token, and recoloured in place by ``apply()`` with ``dpg.set_value`` - switching theme/accent
  never rebuilds widgets or themes. Code that bakes colours into items (``color=`` on texts,
  drawlist colours) checks ``revision()`` to know when to refresh.

Spacing follows a 4/8-px grid; radii: controls 8, cards 12, pills fully rounded.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import dearpygui.dearpygui as dpg

from app.models.camera_state import CameraState

logger = logging.getLogger(__name__)

THEME_TEXT = (-255, 0, 0, 255)  # Dear PyGui sentinel: use the theme's (or bound role's) text colour

# --- tokens ------------------------------------------------------------------------------------
PALETTES: dict[str, dict[str, tuple]] = {
    "dark": {
        "canvas": (16, 18, 23),           # main window background
        "surface": (23, 26, 32),          # sidebar / section panels
        "card": (31, 35, 43),             # cards inside surfaces, popups, floating windows
        "raised": (37, 42, 51),           # floating window background
        "control": (42, 47, 57),          # buttons, inputs, combos
        "control_hover": (52, 58, 70),
        "control_active": (63, 70, 84),
        "segment_on": (74, 82, 98),       # selected segment of a segmented control
        "separator": (45, 50, 60),
        "border": (52, 58, 69),
        "text": (236, 239, 244),
        "text_secondary": (150, 158, 173),
        "text_tertiary": (100, 108, 123),
        "bar": (12, 14, 18),              # menu bar, status bar, window title bars
        "stage": (6, 7, 9),               # letterbox behind camera images
        "row_alt": (255, 255, 255, 7),
        "scrollbar": (255, 255, 255, 38),
        "on_accent": (255, 255, 255),
        "knob": (255, 255, 255),
        "shadow": (0, 0, 0, 110),
        "modal_dim": (0, 0, 0, 120),
    },
    "light": {
        "canvas": (238, 240, 244),
        "surface": (249, 250, 252),
        "card": (255, 255, 255),
        "raised": (252, 252, 254),
        "control": (232, 235, 240),
        "control_hover": (221, 225, 232),
        "control_active": (208, 213, 222),
        "segment_on": (255, 255, 255),
        "separator": (225, 228, 234),
        "border": (214, 218, 226),
        "text": (20, 23, 30),
        "text_secondary": (90, 98, 112),
        "text_tertiary": (145, 152, 166),
        "bar": (250, 251, 253),
        "stage": (20, 22, 27),            # camera images stay on a dark stage in light mode too
        "row_alt": (20, 30, 60, 8),
        "scrollbar": (20, 30, 60, 45),
        "on_accent": (255, 255, 255),
        "knob": (255, 255, 255),
        "shadow": (20, 30, 60, 60),
        "modal_dim": (20, 24, 32, 70),
    },
}

# name -> (dark variant, light variant). Status hues (green/amber/red) are deliberately absent.
ACCENTS: dict[str, tuple[tuple, tuple]] = {
    "Azure": ((64, 145, 255), (16, 104, 232)),
    "Indigo": ((123, 117, 255), (84, 76, 222)),
    "Violet": ((178, 118, 255), (138, 70, 222)),
    "Teal": ((38, 198, 186), (0, 140, 134)),
    "Graphite": ((150, 160, 178), (88, 98, 116)),
}
DEFAULT_ACCENT = "Azure"
THEMES = ("dark", "light")

_STATUS = {
    "dark": {"success": (52, 211, 125), "warning": (255, 178, 46), "error": (255, 90, 90)},
    "light": {"success": (20, 158, 80), "warning": (204, 118, 0), "error": (222, 44, 44)},
}

# One vocabulary for camera states everywhere (sidebar, tiles, status table, performance).
STATE_NAMES = {
    CameraState.DISCONNECTED: "Off",
    CameraState.CONNECTED: "Standby",
    CameraState.ACQUIRING: "Live",
    CameraState.ERROR: "Error",
}

COLORS: dict[str, tuple] = {}
STATE_COLORS: dict[CameraState, tuple] = {}
# Back-compat names (read at import time by older code); prefer COLORS[...] at use time.
TEXT_DIM = PALETTES["dark"]["text_secondary"]
WARNING_COLOR = _STATUS["dark"]["warning"]

_current = {"theme": "dark", "accent": DEFAULT_ACCENT, "revision": 0}


def _mix(a: tuple, b: tuple, t: float) -> tuple:
    return tuple(round(x + (y - x) * t) for x, y in zip(a[:3], b[:3]))


def _resolve(name: str, accent: str) -> None:
    name = name if name in PALETTES else "dark"
    accent = accent if accent in ACCENTS else DEFAULT_ACCENT
    p, status = PALETTES[name], _STATUS[name]
    dark = name == "dark"
    acc = ACCENTS[accent][0 if dark else 1]
    COLORS.clear()
    COLORS.update(p)
    COLORS.update(status)
    COLORS["accent"] = acc
    COLORS["accent_hover"] = _mix(acc, (255, 255, 255), 0.14) if dark else _mix(acc, (0, 0, 0), 0.10)
    COLORS["accent_active"] = _mix(acc, (0, 0, 0), 0.12)
    COLORS["accent_soft"] = (*acc, 52 if dark else 34)        # selection / tinted backgrounds
    COLORS["accent_softer"] = (*acc, 30 if dark else 20)
    COLORS["accent_text"] = _mix(acc, (255, 255, 255), 0.25) if dark else acc
    COLORS["error_hover"] = _mix(status["error"], (255, 255, 255), 0.12)
    STATE_COLORS[CameraState.DISCONNECTED] = p["text_tertiary"]
    STATE_COLORS[CameraState.CONNECTED] = acc
    STATE_COLORS[CameraState.ACQUIRING] = status["success"]
    STATE_COLORS[CameraState.ERROR] = status["error"]
    _current["theme"], _current["accent"] = name, accent


_resolve("dark", DEFAULT_ACCENT)


def current_theme() -> str:
    return _current["theme"]


def current_accent() -> str:
    return _current["accent"]


def revision() -> int:
    """Incremented on every apply(); compare to know when baked-in colours need refreshing."""
    return _current["revision"]


def color(token: str, alpha: int | None = None) -> tuple:
    value = COLORS[token]
    return (*value[:3], alpha) if alpha is not None else value


# --- registered (recolourable) theme colours ------------------------------------------------
_registry: list[tuple[int | str, str, int | None]] = []
_roles: dict[str, int | str] = {}
_listeners: list[Callable[[], None]] = []


def _col(target: int, token: str, alpha: int | None = None) -> None:
    item = dpg.add_theme_color(target, color(token, alpha))
    _registry.append((item, token, alpha))


def on_apply(callback: Callable[[], None]) -> None:
    """Called after every apply() (e.g. to regenerate textures drawn in theme colours)."""
    _listeners.append(callback)


def apply(name: str = "dark", accent: str | None = None) -> int:
    """Switch theme and/or accent live; binds and returns the global theme."""
    _resolve(name, accent or _current["accent"])
    _current["revision"] += 1
    alive = []
    for item, token, alpha in _registry:
        if dpg.does_item_exist(item):
            dpg.set_value(item, list(color(token, alpha)))
            alive.append((item, token, alpha))
    _registry[:] = alive
    for callback in list(_listeners):
        try:
            callback()
        except Exception:  # noqa: BLE001 - a stale listener must never break theme switching
            logger.debug("theme listener failed", exc_info=True)
    theme = role("global")
    dpg.bind_theme(theme)
    return theme


def create_theme(name: str = "dark") -> int:
    """Back-compat: apply the theme (keeping the current accent) and return the global theme."""
    return apply(name)


_CONTEXT_MARKER = "__ui_theme_context__"
_context = {"generation": 0}


def context_generation() -> int:
    """Changes whenever a new Dear PyGui context is in use. Item ids are reused across contexts
    (tests create one per test), so caches of item ids must be dropped when this changes."""
    if not dpg.does_alias_exist(_CONTEXT_MARKER):
        dpg.add_value_registry(tag=_CONTEXT_MARKER)
        _context["generation"] += 1
        _roles.clear()
        _registry.clear()
        _text_widths.clear()
    return _context["generation"]


def role(name: str) -> int | str:
    """Theme for a component role (see _ROLE_BUILDERS); created once per Dear PyGui context."""
    context_generation()
    theme = _roles.get(name)
    if theme is None or not dpg.does_item_exist(theme):
        with dpg.theme() as theme:
            _ROLE_BUILDERS[name]()
        _roles[name] = theme
    return theme


def bind(item: int | str, role_name: str) -> None:
    dpg.bind_item_theme(item, role(role_name))


# --- role builders -----------------------------------------------------------------------------
def _global() -> None:
    with dpg.theme_component(dpg.mvAll):
        _col(dpg.mvThemeCol_WindowBg, "raised")
        _col(dpg.mvThemeCol_ChildBg, "surface")
        _col(dpg.mvThemeCol_PopupBg, "card")
        _col(dpg.mvThemeCol_FrameBg, "control")
        _col(dpg.mvThemeCol_FrameBgHovered, "control_hover")
        _col(dpg.mvThemeCol_FrameBgActive, "control_active")
        _col(dpg.mvThemeCol_Text, "text")
        _col(dpg.mvThemeCol_TextDisabled, "text_tertiary")
        _col(dpg.mvThemeCol_Border, "border")
        dpg.add_theme_color(dpg.mvThemeCol_BorderShadow, (0, 0, 0, 0))
        _col(dpg.mvThemeCol_Button, "control")
        _col(dpg.mvThemeCol_ButtonHovered, "control_hover")
        _col(dpg.mvThemeCol_ButtonActive, "control_active")
        _col(dpg.mvThemeCol_Header, "accent_soft")
        _col(dpg.mvThemeCol_HeaderHovered, "accent_softer")
        _col(dpg.mvThemeCol_HeaderActive, "accent_soft")
        _col(dpg.mvThemeCol_CheckMark, "accent")
        _col(dpg.mvThemeCol_SliderGrab, "accent")
        _col(dpg.mvThemeCol_SliderGrabActive, "accent_hover")
        _col(dpg.mvThemeCol_TextSelectedBg, "accent_soft")
        _col(dpg.mvThemeCol_NavHighlight, "accent")
        _col(dpg.mvThemeCol_DragDropTarget, "accent")
        _col(dpg.mvThemeCol_MenuBarBg, "bar")
        _col(dpg.mvThemeCol_TitleBg, "bar")
        _col(dpg.mvThemeCol_TitleBgActive, "bar")
        _col(dpg.mvThemeCol_TitleBgCollapsed, "bar")
        _col(dpg.mvThemeCol_TableHeaderBg, "card")
        dpg.add_theme_color(dpg.mvThemeCol_TableRowBg, (0, 0, 0, 0))
        _col(dpg.mvThemeCol_TableRowBgAlt, "row_alt")
        _col(dpg.mvThemeCol_TableBorderStrong, "separator")
        _col(dpg.mvThemeCol_TableBorderLight, "separator")
        _col(dpg.mvThemeCol_Separator, "separator")
        _col(dpg.mvThemeCol_SeparatorHovered, "accent")
        _col(dpg.mvThemeCol_SeparatorActive, "accent")
        dpg.add_theme_color(dpg.mvThemeCol_ScrollbarBg, (0, 0, 0, 0))
        _col(dpg.mvThemeCol_ScrollbarGrab, "scrollbar")
        _col(dpg.mvThemeCol_ScrollbarGrabHovered, "text_tertiary")
        _col(dpg.mvThemeCol_ScrollbarGrabActive, "text_secondary")
        dpg.add_theme_color(dpg.mvThemeCol_ResizeGrip, (0, 0, 0, 0))
        _col(dpg.mvThemeCol_ResizeGripHovered, "accent_soft")
        _col(dpg.mvThemeCol_ResizeGripActive, "accent")
        _col(dpg.mvThemeCol_ModalWindowDimBg, "modal_dim")
        _col(dpg.mvThemeCol_Tab, "control")
        _col(dpg.mvThemeCol_TabHovered, "control_hover")
        _col(dpg.mvThemeCol_TabActive, "accent_soft")
        dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 12)
        dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 12)
        dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 8)
        dpg.add_theme_style(dpg.mvStyleVar_PopupRounding, 10)
        dpg.add_theme_style(dpg.mvStyleVar_GrabRounding, 8)
        dpg.add_theme_style(dpg.mvStyleVar_TabRounding, 8)
        dpg.add_theme_style(dpg.mvStyleVar_ScrollbarRounding, 8)
        dpg.add_theme_style(dpg.mvStyleVar_ScrollbarSize, 10)
        dpg.add_theme_style(dpg.mvStyleVar_GrabMinSize, 12)
        dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 16, 14)
        dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 10, 5)
        dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 8, 8)
        dpg.add_theme_style(dpg.mvStyleVar_ItemInnerSpacing, 8, 6)
        dpg.add_theme_style(dpg.mvStyleVar_CellPadding, 10, 5)
        dpg.add_theme_style(dpg.mvStyleVar_IndentSpacing, 20)
        dpg.add_theme_style(dpg.mvStyleVar_WindowBorderSize, 1)
        dpg.add_theme_style(dpg.mvStyleVar_ChildBorderSize, 0)
        dpg.add_theme_style(dpg.mvStyleVar_PopupBorderSize, 1)
        dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 0)
        dpg.add_theme_style(dpg.mvStyleVar_WindowTitleAlign, 0.0, 0.5)
        dpg.add_theme_style(dpg.mvStyleVar_SelectableTextAlign, 0.0, 0.5)
    # Collapsing headers / tree nodes: quiet, no filled strip; hover is a soft neutral wash.
    for item_type in (dpg.mvCollapsingHeader, dpg.mvTreeNode):
        with dpg.theme_component(item_type):
            dpg.add_theme_color(dpg.mvThemeCol_Header, (0, 0, 0, 0))
            _col(dpg.mvThemeCol_HeaderHovered, "control")
            _col(dpg.mvThemeCol_HeaderActive, "control_hover")
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 8)
    # Menus: accent-tinted highlight.
    for item_type in (dpg.mvMenuItem, dpg.mvMenu):
        with dpg.theme_component(item_type):
            _col(dpg.mvThemeCol_HeaderHovered, "accent_soft")
            _col(dpg.mvThemeCol_Header, "accent_soft")
            _col(dpg.mvThemeCol_HeaderActive, "accent_soft")
    # Disabled widgets must look disabled (by default they are indistinguishable).
    for item_type in (dpg.mvButton, dpg.mvImageButton, dpg.mvInputFloat, dpg.mvInputInt, dpg.mvInputText,
                      dpg.mvInputDouble, dpg.mvSliderFloat, dpg.mvCombo, dpg.mvCheckbox):
        with dpg.theme_component(item_type, enabled_state=False):
            _col(dpg.mvThemeCol_Text, "text_tertiary")
            _col(dpg.mvThemeCol_Button, "control", 110)
            _col(dpg.mvThemeCol_ButtonHovered, "control", 110)
            _col(dpg.mvThemeCol_ButtonActive, "control", 110)
            _col(dpg.mvThemeCol_FrameBg, "control", 110)
            _col(dpg.mvThemeCol_FrameBgHovered, "control", 110)
            _col(dpg.mvThemeCol_CheckMark, "text_tertiary")


def _square_window() -> None:
    """Primary window: fills the viewport, so rounded corners/borders would show gaps."""
    with dpg.theme_component(dpg.mvWindowAppItem):
        _col(dpg.mvThemeCol_WindowBg, "canvas")
        dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 0)
        dpg.add_theme_style(dpg.mvStyleVar_WindowBorderSize, 0)
        dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 12, 8)


def _surface() -> None:
    """Sidebar and section panels."""
    with dpg.theme_component(dpg.mvChildWindow):
        _col(dpg.mvThemeCol_ChildBg, "surface")
        _col(dpg.mvThemeCol_Border, "separator")
        dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 12)
        dpg.add_theme_style(dpg.mvStyleVar_ChildBorderSize, 1)
        dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 12, 12)


def _card() -> None:
    """A card inside a surface (camera rows)."""
    with dpg.theme_component(dpg.mvChildWindow):
        _col(dpg.mvThemeCol_ChildBg, "card")
        _col(dpg.mvThemeCol_Border, "separator")
        dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 10)
        dpg.add_theme_style(dpg.mvStyleVar_ChildBorderSize, 1)
        dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 10, 8)


def _card_selected() -> None:
    with dpg.theme_component(dpg.mvChildWindow):
        _col(dpg.mvThemeCol_ChildBg, "card")
        _col(dpg.mvThemeCol_Border, "accent")
        dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 10)
        dpg.add_theme_style(dpg.mvStyleVar_ChildBorderSize, 1)
        dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 10, 8)


def _canvas() -> None:
    """Transparent container (multiview area, header and status bar contents)."""
    with dpg.theme_component(dpg.mvChildWindow):
        dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (0, 0, 0, 0))
        dpg.add_theme_style(dpg.mvStyleVar_ChildBorderSize, 0)
        dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 0, 0)
        dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 0)


def _bar() -> None:
    """Status bar strip."""
    with dpg.theme_component(dpg.mvChildWindow):
        _col(dpg.mvThemeCol_ChildBg, "bar")
        _col(dpg.mvThemeCol_Border, "separator")
        dpg.add_theme_style(dpg.mvStyleVar_ChildBorderSize, 1)
        dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 8)
        dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 12, 4)


def _text(token: str) -> Callable[[], None]:
    def build() -> None:
        with dpg.theme_component(dpg.mvAll):
            _col(dpg.mvThemeCol_Text, token)
    return build


def _ghost() -> None:
    """Background-less icon button (disclosure arrows, ··· menus)."""
    for enabled in (True, False):
        with dpg.theme_component(dpg.mvButton, enabled_state=enabled):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (0, 0, 0, 0))
            _col(dpg.mvThemeCol_ButtonHovered, "control")
            _col(dpg.mvThemeCol_ButtonActive, "control_hover")
            _col(dpg.mvThemeCol_Text, "text_secondary" if enabled else "text_tertiary")
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 4, 4)
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 6)


def _filled(fill: str, hover: str, active: str, text: str) -> Callable[[], None]:
    def build() -> None:
        with dpg.theme_component(dpg.mvButton):
            _col(dpg.mvThemeCol_Button, fill)
            _col(dpg.mvThemeCol_ButtonHovered, hover)
            _col(dpg.mvThemeCol_ButtonActive, active)
            _col(dpg.mvThemeCol_Text, text)
    return build


def _tinted_text(token: str) -> Callable[[], None]:
    """Neutral button with coloured label (idle Record button: red label)."""
    def build() -> None:
        with dpg.theme_component(dpg.mvButton):
            _col(dpg.mvThemeCol_Text, token)
    return build


def _segment_track() -> None:
    with dpg.theme_component(dpg.mvChildWindow):
        _col(dpg.mvThemeCol_ChildBg, "control")
        dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 9)
        dpg.add_theme_style(dpg.mvStyleVar_ChildBorderSize, 0)
        dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 2, 2)
        dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 2, 0)
    with dpg.theme_component(dpg.mvButton):
        dpg.add_theme_color(dpg.mvThemeCol_Button, (0, 0, 0, 0))
        _col(dpg.mvThemeCol_ButtonHovered, "control_hover")
        _col(dpg.mvThemeCol_ButtonActive, "control_active")
        _col(dpg.mvThemeCol_Text, "text_secondary")
        dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 7)
        dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 8, 3)


def _segment_on() -> None:
    with dpg.theme_component(dpg.mvButton):
        _col(dpg.mvThemeCol_Button, "segment_on")
        _col(dpg.mvThemeCol_ButtonHovered, "segment_on")
        _col(dpg.mvThemeCol_ButtonActive, "segment_on")
        _col(dpg.mvThemeCol_Text, "text")
        dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 7)
        dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 8, 3)


def _pill(fill: str | None, fill_alpha: int | None, text: str) -> Callable[[], None]:
    """Non-interactive capsule badge (implemented as a button whose hover = idle)."""
    def build() -> None:
        with dpg.theme_component(dpg.mvButton):
            for target in (dpg.mvThemeCol_Button, dpg.mvThemeCol_ButtonHovered, dpg.mvThemeCol_ButtonActive):
                if fill is None:
                    dpg.add_theme_color(target, (0, 0, 0, 140))
                else:
                    _col(target, fill, fill_alpha)
            _col(dpg.mvThemeCol_Text, text)
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 12)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 9, 1)
    return build


def _callout(token: str) -> Callable[[], None]:
    """Tinted notice box (e.g. the Property Grid's "streaming locks some features" note)."""
    def build() -> None:
        with dpg.theme_component(dpg.mvChildWindow):
            _col(dpg.mvThemeCol_ChildBg, token, 30)
            _col(dpg.mvThemeCol_Border, token, 90)
            dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 8)
            dpg.add_theme_style(dpg.mvStyleVar_ChildBorderSize, 1)
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 12, 8)
        with dpg.theme_component(dpg.mvText):
            _col(dpg.mvThemeCol_Text, "text")
    return build


def _stat_tile() -> None:
    with dpg.theme_component(dpg.mvChildWindow):
        _col(dpg.mvThemeCol_ChildBg, "card")
        _col(dpg.mvThemeCol_Border, "separator")
        dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 10)
        dpg.add_theme_style(dpg.mvStyleVar_ChildBorderSize, 1)
        dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 12, 8)
        dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 8, 2)


def _quiet_selectable() -> None:
    """Selectable without a selection fill (selection is shown by the surrounding card)."""
    with dpg.theme_component(dpg.mvSelectable):
        dpg.add_theme_color(dpg.mvThemeCol_Header, (0, 0, 0, 0))
        dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, (0, 0, 0, 0))
        dpg.add_theme_color(dpg.mvThemeCol_HeaderActive, (0, 0, 0, 0))


def _overlay() -> None:
    """Transparent overlay strip on a camera tile (its gradient is drawn by a drawlist)."""
    with dpg.theme_component(dpg.mvChildWindow):
        dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (0, 0, 0, 0))
        dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 0)
        dpg.add_theme_style(dpg.mvStyleVar_ChildBorderSize, 0)
        dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 0, 0)
        dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 0, 0)


def _tile() -> None:
    with dpg.theme_component(dpg.mvChildWindow):
        _col(dpg.mvThemeCol_ChildBg, "stage")
        dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 0)  # corners are masked by the overlays
        dpg.add_theme_style(dpg.mvStyleVar_ChildBorderSize, 0)
        dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 0, 0)


def _tile_empty() -> None:
    """Unassigned multiview slot: recedes into the canvas instead of a black hole."""
    with dpg.theme_component(dpg.mvChildWindow):
        _col(dpg.mvThemeCol_ChildBg, "surface")
        dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 0)
        dpg.add_theme_style(dpg.mvStyleVar_ChildBorderSize, 0)
        dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 0, 0)


def _compact_table() -> None:
    with dpg.theme_component(dpg.mvAll):
        dpg.add_theme_style(dpg.mvStyleVar_CellPadding, 10, 3)
        dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 8, 2)


def _tight() -> None:
    """Groups of controls inside cards: slightly tighter vertical rhythm."""
    with dpg.theme_component(dpg.mvAll):
        dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 8, 6)
        dpg.add_theme_style(dpg.mvStyleVar_CellPadding, 0, 0)


def _stack() -> None:
    """Vertical group with no item spacing: a spacer inside it nudges content down by exactly
    its height (used to align controls of different heights on one line)."""
    with dpg.theme_component(dpg.mvAll):
        dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 8, 0)


def nudge(dy: int, parent: int | str | None = None):
    """Context manager: a group whose content is shifted down by ``dy`` pixels."""
    kw = {"parent": parent} if parent is not None else {}
    group = dpg.group(**kw)

    class _Nudge:
        def __enter__(self_inner):
            item = group.__enter__()
            bind(item, "stack")
            dpg.add_spacer(height=dy)
            return item

        def __exit__(self_inner, *exc):
            return group.__exit__(*exc)

    return _Nudge()


def _image_button() -> None:
    for enabled in (True, False):
        with dpg.theme_component(dpg.mvImageButton, enabled_state=enabled):
            for target in (dpg.mvThemeCol_Button, dpg.mvThemeCol_ButtonHovered, dpg.mvThemeCol_ButtonActive):
                dpg.add_theme_color(target, (0, 0, 0, 0))
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 0, 0)


def _search() -> None:
    with dpg.theme_component(dpg.mvInputText):
        dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 14)
        dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 12, 5)


def _dialog() -> None:
    with dpg.theme_component(dpg.mvAll):
        dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 20, 16)
        dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 8, 10)


_ROLE_BUILDERS: dict[str, Callable[[], None]] = {
    "global": _global,
    "square_window": _square_window,
    "surface": _surface,
    "card": _card,
    "card_selected": _card_selected,
    "canvas": _canvas,
    "bar": _bar,
    "text_secondary": _text("text_secondary"),
    "text_tertiary": _text("text_tertiary"),
    "text_accent": _text("accent_text"),
    "ghost": _ghost,
    "primary": _filled("accent", "accent_hover", "accent_active", "on_accent"),
    "danger": _filled("error", "error_hover", "error", "on_accent"),
    "success": _filled("success", "success", "success", "on_accent"),
    "record_idle": _tinted_text("error"),
    "segment_track": _segment_track,
    "segment_on": _segment_on,
    "pill_neutral": _pill("control", None, "text_secondary"),
    "pill_success": _pill("success", 40, "success"),
    "pill_accent": _pill("accent", 40, "accent_text"),
    "pill_error": _pill("error", 40, "error"),
    "pill_rec": _pill("error", None, "on_accent"),
    "quiet_selectable": _quiet_selectable,
    "callout_warning": _callout("warning"),
    "stat_tile": _stat_tile,
    "overlay": _overlay,
    "tile": _tile,
    "tile_empty": _tile_empty,
    "compact_table": _compact_table,
    "tight": _tight,
    "image_button": _image_button,
    "stack": _stack,
    "search": _search,
    "dialog": _dialog,
}


# Back-compat helpers (older call sites) ----------------------------------------------------
def compact_table_theme() -> int | str:
    return role("compact_table")


def plain_button_theme() -> int | str:
    return role("ghost")


def segment_selected_theme(name: str = "dark") -> int | str:
    return role("segment_on")


def square_window_theme() -> int | str:
    return role("square_window")


# --- fonts -------------------------------------------------------------------------------------
# First existing file wins for each role. Dear PyGui's built-in font lacks glyphs such as the
# state dot (U+25CF), so a system font is required for the intended look. Segoe UI also lacks
# U+22EE and U+25B6: use "···", U+25BA ► and U+25A0 ■.
_FONT_DIR = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
_REGULAR = (_FONT_DIR / "segoeui.ttf", Path("/System/Library/Fonts/SFNS.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
_SEMIBOLD = (_FONT_DIR / "seguisb.ttf", _FONT_DIR / "segoeuib.ttf",
             Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))
_MONO = (_FONT_DIR / "consola.ttf", Path("/System/Library/Fonts/SFNSMono.ttf"),
         Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"))
# role -> (candidates, pixel size). Type scale: caption 13 / small 14 / body 16 / title 21 / metric 19.
FONT_CANDIDATES = {
    "body": (_REGULAR, 16),
    "heading": (_SEMIBOLD, 16),
    "title": (_SEMIBOLD, 21),
    "metric": (_SEMIBOLD, 19),
    "caption": (_SEMIBOLD, 13),
    "small": (_REGULAR, 14),
    "mono": (_MONO, 14),
}
FONT_SIZES = {role_: size for role_, (_c, size) in FONT_CANDIDATES.items()}


@dataclass(frozen=True)
class Fonts:
    body: int | str | None = None
    heading: int | str | None = None
    title: int | str | None = None
    metric: int | str | None = None
    caption: int | str | None = None
    small: int | str | None = None
    mono: int | str | None = None


_fonts = Fonts()


def load_fonts() -> Fonts:
    """Load all font roles (missing ones fall back to the body/default font)."""
    global _fonts
    loaded: dict[str, int | str | None] = {}
    with dpg.font_registry():
        for role_, (candidates, size) in FONT_CANDIDATES.items():
            path = next((p for p in candidates if p.exists()), None)
            loaded[role_] = dpg.add_font(str(path), size) if path else None
    body = loaded["body"]
    if body is None:
        logger.warning("No UI font found; using Dear PyGui default (state dots may render as '?')")
    else:
        dpg.bind_font(body)
    _fonts = Fonts(**{k: (v if v is not None else body) for k, v in loaded.items()})
    return _fonts


def load_font(size: int = 16) -> None:
    """Backwards-compatible alias used by older callers."""
    load_fonts()


def fonts() -> Fonts:
    return _fonts


def use_font(item: int | str, role_name: str) -> None:
    """Apply a font role to an item if fonts are loaded."""
    font = getattr(_fonts, role_name, None)
    if font is not None:
        dpg.bind_item_font(item, font)


_text_widths: dict[tuple, float] = {}


def text_width(text: str, role_name: str = "body") -> float:
    """Rendered width of ``text`` in a font role. Before the font atlas exists (first frame)
    Dear PyGui cannot measure, so an estimate is returned and not cached."""
    font = getattr(_fonts, role_name, None)
    key = (text, font)
    if key in _text_widths:
        return _text_widths[key]
    try:
        size = dpg.get_text_size(text, font=font) if font is not None else dpg.get_text_size(text)
    except Exception:  # noqa: BLE001 - measuring is best effort
        size = None
    if not size or size[0] <= 0:
        return len(text) * FONT_SIZES.get(role_name, 16) * 0.55
    _text_widths[key] = size[0]
    return size[0]


# --- small builders used across panels --------------------------------------------------------
def caption(text: str, parent: int | str | None = None, upper: bool = True, **kwargs) -> int | str:
    """Section caption in the caption font and tertiary colour (upper-case unless ``upper=False``,
    e.g. for units such as "Mb/s", which must never read "MB/S")."""
    kw = {"parent": parent} if parent is not None else {}
    item = dpg.add_text(text.upper() if upper else text, **kw, **kwargs)
    use_font(item, "caption")
    bind(item, "text_tertiary")
    return item


def secondary_text(text: str = "", parent: int | str | None = None, **kwargs) -> int | str:
    """Text in the secondary colour; ``configure_item(color=...)`` can still override it, and
    ``color=THEME_TEXT`` returns it to the secondary colour."""
    kw = {"parent": parent} if parent is not None else {}
    item = dpg.add_text(text, **kw, **kwargs)
    bind(item, "text_secondary")
    return item


class SegmentedControl:
    """Pill-shaped segmented control: a rounded track with the selected segment raised."""

    def __init__(self, parent: int | str | None, labels: list[str] | tuple[str, ...], selected: str,
                 on_select: Callable[[str], None], segment_width: int = 64, height: int = 30) -> None:
        self._on_select = on_select
        self.selected = selected
        self.buttons: dict[str, int | str] = {}
        width = len(labels) * segment_width + (len(labels) - 1) * 2 + 4
        kw = {"parent": parent} if parent is not None else {}
        with dpg.child_window(width=width, height=height, no_scrollbar=True, no_scroll_with_mouse=True,
                              **kw) as self.track:
            with dpg.group(horizontal=True, horizontal_spacing=2):
                for label in labels:
                    self.buttons[label] = dpg.add_button(label=label, width=segment_width, height=height - 4,
                                                         callback=lambda _s, _a, lab: self._clicked(lab),
                                                         user_data=label)
        bind(self.track, "segment_track")
        self._paint()

    def _clicked(self, label: str) -> None:
        self.set(label)
        self._on_select(label)

    def set(self, label: str) -> None:
        if label in self.buttons:
            self.selected = label
            self._paint()

    def _paint(self) -> None:
        for label, button in self.buttons.items():
            dpg.bind_item_theme(button, role("segment_on") if label == self.selected else 0)
