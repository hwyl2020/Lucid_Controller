"""Custom-drawn widgets that Dear PyGui has no native look for.

``Switch``: a toggle switch (rounded track + knob, green when on) drawn into small anti-aliased
textures and shown with an image button. The textures are shared by all switches and redrawn in
the new colours whenever the theme or accent changes (``theme.on_apply``).

``Splitter``: a drag handle between panes (sidebar | multiview, stream area / bottom sections).
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


class Splitter:
    """Drag handle that resizes a pane: a thin column (``vertical``, changes a width) or a thin row
    (changes a height). ``sign`` is +1 when dragging right/down grows the pane, -1 when dragging
    left/up grows it. ``update()`` must run every frame; ``on_release`` gets the final size."""

    def __init__(self, parent: int | str | None, vertical: bool, get_size: Callable[[], float],
                 set_size: Callable[[float], None], minimum: float, maximum: Callable[[], float],
                 sign: int = 1, thickness: int = 6, on_release: Callable[[float], None] | None = None,
                 tooltip: str = "Drag to resize") -> None:
        kw = {"parent": parent} if parent is not None else {}
        self.vertical = vertical
        self._get, self._set = get_size, set_size
        self._min, self._max = minimum, maximum
        self._sign = sign
        self._on_release = on_release
        self._drag: tuple[float, float] | None = None  # (mouse coordinate, size) when the drag began
        self.button = dpg.add_button(label="", width=thickness if vertical else -1,
                                     height=-1 if vertical else thickness, **kw)
        theme.bind(self.button, "splitter")
        with dpg.tooltip(self.button, delay=0.6):
            dpg.add_text(tooltip)

    def clamp(self, size: float) -> int:
        return int(round(min(max(size, self._min), max(self._min, self._max()))))

    def update(self) -> None:
        if not dpg.is_item_active(self.button):
            if self._drag is not None:
                self._drag = None
                if self._on_release is not None:
                    self._on_release(self._get())
            return
        x, y = dpg.get_mouse_pos(local=False)
        mouse = x if self.vertical else y
        if self._drag is None:
            self._drag = (mouse, self._get())
            return
        start_mouse, start_size = self._drag
        size = self.clamp(start_size + self._sign * (mouse - start_mouse))
        if size != self._get():
            self._set(size)

    @property
    def dragging(self) -> bool:
        return self._drag is not None


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
