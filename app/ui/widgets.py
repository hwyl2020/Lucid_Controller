"""Custom-drawn widgets that Dear PyGui has no native look for.

``Switch``: a toggle switch (rounded track + knob, green when on) drawn into small anti-aliased
textures and shown with an image button. The textures are shared by all switches and redrawn in
the new colours whenever the theme or accent changes (``theme.on_apply``).
"""

from __future__ import annotations

from collections.abc import Callable

import dearpygui.dearpygui as dpg
import numpy as np

from app.ui import theme

SWITCH_W, SWITCH_H = 40, 22
_KNOB_R = 9.0

_textures: dict[str, int | str] = {}
_listening = False
_generation = {"value": -1}


def _coverage(sdf: np.ndarray) -> np.ndarray:
    return np.clip(0.5 - sdf, 0.0, 1.0)


def _over(dst: np.ndarray, rgb: tuple, alpha: np.ndarray) -> None:
    """Composite a solid colour with per-pixel alpha over dst (straight alpha, float RGBA)."""
    src = np.array(rgb[:3], np.float32) / 255.0
    a_dst = dst[..., 3]
    a_out = alpha + a_dst * (1 - alpha)
    safe = np.where(a_out > 0, a_out, 1)
    for c in range(3):
        dst[..., c] = (src[c] * alpha + dst[..., c] * a_dst * (1 - alpha)) / safe
    dst[..., 3] = a_out


def switch_rgba(on: bool, enabled: bool, track_on: tuple, track_off: tuple, knob: tuple) -> np.ndarray:
    h, w = SWITCH_H, SWITCH_W
    y, x = np.mgrid[0:h, 0:w].astype(np.float32) + 0.5
    r = h / 2
    # Rounded track (capsule) SDF.
    cx = np.clip(x, r, w - r)
    track = np.hypot(x - cx, y - r) - (r - 0.5)
    img = np.zeros((h, w, 4), np.float32)
    _over(img, track_on if on else track_off, _coverage(track))
    kx = w - r if on else r
    shadow = np.hypot(x - kx, y - r - 1.0) - (_KNOB_R + 0.6)
    _over(img, (0, 0, 0), np.clip(0.5 - shadow / 1.6, 0, 1) * 0.28)
    _over(img, knob, _coverage(np.hypot(x - kx, y - r) - _KNOB_R))
    if not enabled:
        img[..., 3] *= 0.45
    return img


def _paint() -> None:
    track_on, track_off = theme.color("success"), theme.color("control_active")
    knob = theme.color("knob")
    for key in list(_textures):
        on, enabled = key.startswith("on"), not key.endswith("disabled")
        if dpg.does_item_exist(_textures[key]):
            dpg.set_value(_textures[key], switch_rgba(on, enabled, track_on, track_off, knob).ravel())


def switch_texture(on: bool, enabled: bool = True) -> int | str:
    global _listening
    generation = theme.context_generation()
    if generation != _generation["value"] or not _textures:
        _generation["value"] = generation
        _textures.clear()
        registry = dpg.add_texture_registry()
        for key in ("on", "off", "on_disabled", "off_disabled"):
            _textures[key] = dpg.add_dynamic_texture(SWITCH_W, SWITCH_H, np.zeros(SWITCH_W * SWITCH_H * 4, np.float32),
                                                     parent=registry)
        _paint()
        if not _listening:
            theme.on_apply(_paint)
            _listening = True
    return _textures[("on" if on else "off") + ("" if enabled else "_disabled")]


class Switch:
    """Toggle switch. The image button keeps a text label ("ON"/"OFF"/pending text) that is not
    drawn but describes the state (tests and tooltips read it)."""

    def __init__(self, parent: int | str | None, callback: Callable[[], None], label: str = "OFF") -> None:
        kw = {"parent": parent} if parent is not None else {}
        self._state: tuple[bool, bool] | None = None
        self.button = dpg.add_image_button(switch_texture(False), width=SWITCH_W, height=SWITCH_H, label=label,
                                           callback=lambda: callback(), **kw)
        theme.bind(self.button, "image_button")
        self.set(False, True, label)

    def set(self, on: bool, enabled: bool, label: str) -> None:
        if (on, enabled) != self._state:
            self._state = (on, enabled)
            dpg.configure_item(self.button, texture_tag=switch_texture(on, enabled), enabled=enabled)
        if dpg.get_item_label(self.button) != label:
            dpg.configure_item(self.button, label=label)
