"""One multiview tile: live image fitted to the whole tile, with name/state and FPS on overlay bars.

The image always keeps the camera's aspect ratio (no crop, no stretch) and is centred; the tile's
title and statistics sit on translucent gradient bars over the image instead of taking their own
rows, so the image can use the full tile height.

Overlay bars are child windows (drawlists ignore ``pos`` in Dear PyGui, child windows don't) with
a drawlist inside each that paints the gradient, the state capsule and the tile's rounded corners
(corner masks in the canvas colour, since Dear PyGui cannot clip an image to a rounded rect).
"""

from __future__ import annotations

import logging
import math
import time

import dearpygui.dearpygui as dpg
import numpy as np

from app.acquisition.processing import PixelConversionError, to_display_rgba
from app.cameras.camera_manager import CameraManager
from app.models.camera_state import CameraState
from app.models.camera_status import camera_display_name
from app.services.reconnect_service import ReconnectService
from app.services.recording_service import RecordingService
from app.ui import theme
from app.ui.theme import COLORS, STATE_COLORS, bind, text_width, use_font

logger = logging.getLogger(__name__)

BAR_HEIGHT = 40  # overlay bars (top: name + state capsule, bottom: FPS / frame id / errors)
INSET = 0  # image inset inside the tile
TEXT_X = 14
TILE_ROUNDING = 10
GRADIENT_ALPHA = 165
OVERLAY_TEXT = (246, 247, 250)
OVERLAY_TEXT_DIM = (200, 206, 216)
CAPSULE_H = 22
CAPSULE_BG = (24, 27, 34, 200)  # glass capsule, visible on black and bright images
STATE_LABELS = {
    CameraState.DISCONNECTED: "OFF",
    CameraState.CONNECTED: "STANDBY",
    CameraState.ACQUIRING: "LIVE",
    CameraState.ERROR: "ERROR",
}
# Texture long-side sizes. The texture is sized to the image as displayed in the tile (rounded up
# to one of these) so conversion cost scales with what is shown, without recreating textures on
# every resize. Sizing by the tile's longest side instead converted ~20x more pixels than shown in
# short, wide tiles (e.g. with the status/log sections open).
TEXTURE_SIDES = (256, 384, 512, 768, 1024, 1536, 2048)


def texture_side_for(frame_width: int, frame_height: int, box_width: int, box_height: int) -> int:
    """Smallest texture long side that covers the frame fitted (aspect kept) into the box."""
    if frame_width <= 0 or frame_height <= 0 or box_width <= 0 or box_height <= 0:
        return TEXTURE_SIDES[-1]
    scale = min(box_width / frame_width, box_height / frame_height)
    needed = max(frame_width, frame_height) * scale
    return next((s for s in TEXTURE_SIDES if s >= needed), TEXTURE_SIDES[-1])


def fit_image(image_w: int, image_h: int, box_w: int, box_h: int) -> tuple[int, int, int, int]:
    """Largest (x, y, w, h) with the image's aspect ratio that fits the box, centred in it."""
    scale = max(min(box_w / image_w, box_h / image_h), 0.01)
    w, h = max(1, round(image_w * scale)), max(1, round(image_h * scale))
    return (box_w - w) // 2, (box_h - h) // 2, w, h


def _corner_mask(drawlist, cx: float, cy: float, corner: tuple[float, float], fill, radius: float) -> None:
    """Fill the area between a rectangle corner and its rounded arc (triangle fan from the corner)."""
    sx, sy = corner
    start = math.atan2(sy - cy, sx - cx) - math.pi / 4
    steps = 8
    points = [(cx + radius * math.cos(start + i * (math.pi / 2) / steps),
               cy + radius * math.sin(start + i * (math.pi / 2) / steps)) for i in range(steps + 1)]
    for a, b in zip(points, points[1:]):
        dpg.draw_triangle(corner, a, b, color=(0, 0, 0, 0), fill=fill, thickness=0, parent=drawlist)


class CameraView:
    def __init__(self, parent: int | str, texture_registry: int | str) -> None:
        self.camera_id: str | None = None
        self._registry = texture_registry
        self._size = (0, 0)

        self._texture: int | str | None = None
        self._texture_size: tuple[int, int] | None = None
        self._image: int | str | None = None
        self._buffer: np.ndarray | None = None  # reused (h, w, 4) float32; the raw texture reads it

        self._display_frames = 0
        self._display_window_start = time.perf_counter()
        self._display_fps = 0.0
        self._last_frame_id: int | None = None
        self._badge: tuple | None = None  # (label, bg, dot, revision) currently drawn
        self._revision = -1
        self._message_key: tuple | None = None
        self._format_text = ""

        self.tile = dpg.add_child_window(parent=parent, border=False, no_scrollbar=True, no_scroll_with_mouse=True)
        bind(self.tile, "tile")
        # The image is inserted before the bars; the bars (child windows) render above it.
        self._message = dpg.add_text("No camera", parent=self.tile, color=OVERLAY_TEXT_DIM, pos=(TEXT_X, 40))
        use_font(self._message, "heading")
        self._top_bar = dpg.add_child_window(parent=self.tile, width=1, height=BAR_HEIGHT, pos=(0, 0),
                                             border=False, no_scrollbar=True, no_scroll_with_mouse=True)
        self._bottom_bar = dpg.add_child_window(parent=self.tile, width=1, height=BAR_HEIGHT, pos=(0, 0),
                                                border=False, no_scrollbar=True, no_scroll_with_mouse=True)
        for bar in (self._top_bar, self._bottom_bar):
            bind(bar, "overlay")
        self._top_draw = dpg.add_drawlist(1, BAR_HEIGHT, parent=self._top_bar)
        self._bottom_draw = dpg.add_drawlist(1, BAR_HEIGHT, parent=self._bottom_bar)
        self._title = dpg.add_text("", parent=self._top_bar, pos=(TEXT_X, 9), color=OVERLAY_TEXT)
        use_font(self._title, "heading")
        self._dot = dpg.add_text("●", parent=self._top_bar, pos=(0, 11))
        self._state = dpg.add_text("", parent=self._top_bar, pos=(0, 12), color=OVERLAY_TEXT)
        use_font(self._dot, "caption")
        use_font(self._state, "caption")
        self._info = dpg.add_text("", parent=self._bottom_bar, color=OVERLAY_TEXT_DIM, pos=(TEXT_X, 13))
        self._format = dpg.add_text("", parent=self._bottom_bar, color=OVERLAY_TEXT_DIM, pos=(0, 13))
        use_font(self._info, "small")
        use_font(self._format, "small")
        self._capsule = None

    # --- public -----------------------------------------------------------
    def assign(self, camera_id: str | None, manager: CameraManager) -> None:
        if camera_id == self.camera_id:
            return
        self._drop_texture()
        self.camera_id = camera_id
        self._last_frame_id = None
        if camera_id is None:
            title = ""
        else:
            cam = manager.camera(camera_id)
            title = camera_display_name(cam.model, cam.serial_number)
        dpg.set_value(self._title, title)
        dpg.set_item_user_data(self._title, title)
        self._badge = None
        dpg.set_value(self._message, "No camera" if camera_id is None else "Not streaming")
        dpg.show_item(self._message)
        self._message_key = None

    def set_size(self, width: int, height: int) -> None:
        if (width, height) == self._size:
            return
        self._size = (width, height)
        dpg.configure_item(self.tile, width=width, height=height)
        dpg.configure_item(self._top_bar, width=width, height=BAR_HEIGHT)
        dpg.set_item_pos(self._top_bar, [0, 0])
        dpg.configure_item(self._bottom_bar, width=width, height=BAR_HEIGHT)
        dpg.set_item_pos(self._bottom_bar, [0, height - BAR_HEIGHT])
        self._draw_bars()
        self._message_key = None
        self._layout_image()

    @property
    def display_fps(self) -> float:
        return self._display_fps

    def update(
        self,
        manager: CameraManager,
        recording: RecordingService | None = None,
        reconnect: ReconnectService | None = None,
    ) -> None:
        if theme.revision() != self._revision:
            self._draw_bars()  # corner masks use the canvas colour
        self._place_message()
        if self.camera_id is None:
            self._set_badge(None, None, None)
            dpg.set_value(self._info, "")
            return

        state = manager.state(self.camera_id)
        if state is CameraState.ACQUIRING and recording is not None and recording.is_recording(self.camera_id):
            blink = int(time.monotonic() * 2) % 2 == 0
            self._set_badge("REC", (*COLORS["error"], 235), (255, 255, 255) if blink else (255, 255, 255, 90))
        elif state is CameraState.ERROR:
            self._set_badge("ERROR", (*COLORS["error"], 200), (255, 255, 255))
        else:
            self._set_badge(STATE_LABELS[state], CAPSULE_BG, STATE_COLORS[state])

        frame = manager.latest_frame(self.camera_id)
        if frame is not None:
            self._show_frame(frame)
            fmt = f"{frame.width} × {frame.height}  ·  {frame.pixel_format}"
            if fmt != self._format_text:
                self._format_text = fmt
                dpg.set_value(self._format, fmt)
                self._place_format()

        now = time.perf_counter()
        elapsed = now - self._display_window_start
        if elapsed >= 1.0:
            self._display_fps = self._display_frames / elapsed
            self._display_frames, self._display_window_start = 0, now
            self._place_format()  # info width settles once figures are shown

        stats = manager.stats(self.camera_id)
        if stats is None:
            dpg.set_value(self._info, "")
        else:
            frame_id = "–" if stats.last_frame_id is None else f"{stats.last_frame_id:,}"
            dpg.set_value(
                self._info,
                f"{stats.measured_fps:5.1f} fps   ·   display {self._display_fps:4.1f}   ·   #{frame_id}",
            )
        error = manager.last_error(self.camera_id)
        retry = reconnect.state(self.camera_id) if reconnect is not None and error else None
        if retry is not None:
            error = (
                f"Connection lost — reconnecting (attempt {retry.attempts + 1} in {retry.next_attempt_in:.0f}s)"
                if retry.attempts
                else "Connection lost — reconnecting..."
            )
        if error:
            # Keep the last image visible; report the error on the bottom bar (or centre if no image).
            dpg.set_value(self._info, error)
            dpg.configure_item(self._info, color=COLORS["error"])
            if self._image is None:
                dpg.set_value(self._message, error)
                dpg.show_item(self._message)
        else:
            dpg.configure_item(self._info, color=OVERLAY_TEXT_DIM)

    def delete(self) -> None:
        dpg.delete_item(self.tile)
        self._image = None
        self._drop_texture()

    # --- overlay drawing --------------------------------------------------
    def _draw_bars(self) -> None:
        """Gradients and rounded-corner masks; only on resize or theme change."""
        self._revision = theme.revision()
        width = max(1, self._size[0])
        mask = (*COLORS["canvas"][:3], 255)
        r = TILE_ROUNDING
        h = BAR_HEIGHT
        clear, dark = (0, 0, 0, 0), (0, 0, 0, GRADIENT_ALPHA)
        for drawlist, top in ((self._top_draw, True), (self._bottom_draw, False)):
            dpg.delete_item(drawlist, children_only=True)
            dpg.configure_item(drawlist, width=width, height=h)
            colors = [dark, dark, clear, clear] if top else [clear, clear, dark, dark]
            dpg.draw_rectangle((0, 0), (width, h), multicolor=True, corner_colors=colors, fill=dark,
                               color=clear, thickness=0, parent=drawlist)
            if top:
                _corner_mask(drawlist, r, r, (0, 0), mask, r)
                _corner_mask(drawlist, width - r, r, (width, 0), mask, r)
            else:
                _corner_mask(drawlist, r, h - r, (0, h), mask, r)
                _corner_mask(drawlist, width - r, h - r, (width, h), mask, r)
        self._capsule = None
        self._badge = None

    def _set_badge(self, label: str | None, bg, dot) -> None:
        key = (label, bg, dot, self._revision, self._size[0])
        if key == self._badge:
            return
        self._badge = key
        if self._capsule is not None and dpg.does_item_exist(self._capsule):
            dpg.delete_item(self._capsule)
        self._capsule = None
        if label is None:
            dpg.set_value(self._state, "")
            dpg.set_value(self._dot, "")
            return
        label_w = text_width(label, "caption")
        capsule_w = 10 + 10 + 6 + label_w + 12
        x0 = self._size[0] - 12 - capsule_w
        y0 = (BAR_HEIGHT - CAPSULE_H) // 2 - 2
        self._capsule = dpg.draw_rectangle((x0, y0), (x0 + capsule_w, y0 + CAPSULE_H), fill=bg, color=(0, 0, 0, 0),
                                           rounding=CAPSULE_H / 2, thickness=0, parent=self._top_draw)
        dpg.set_value(self._dot, "●")
        dpg.configure_item(self._dot, color=dot)
        dpg.set_item_pos(self._dot, [x0 + 10, y0 + 2])
        dpg.set_value(self._state, label)
        dpg.set_item_pos(self._state, [x0 + 26, y0 + 2])
        self._fit_title(x0 - TEXT_X - 10)

    def _fit_title(self, available: float) -> None:
        """Elide the camera name so it never runs under the state capsule."""
        full = dpg.get_item_user_data(self._title) or dpg.get_value(self._title)
        dpg.set_item_user_data(self._title, full)
        text = full
        while text and text_width(text if text == full else text + "…", "heading") > available:
            text = text[:-1]
        dpg.set_value(self._title, full if text == full else (text.rstrip() + "…" if text else ""))

    def _place_format(self) -> None:
        width = text_width(self._format_text, "small")
        info_w = text_width(dpg.get_value(self._info) or "", "small")
        fits = TEXT_X + info_w + 24 + width + TEXT_X <= self._size[0]
        dpg.configure_item(self._format, show=fits)
        dpg.set_item_pos(self._format, [max(TEXT_X, self._size[0] - TEXT_X - width), 13])

    def _place_message(self) -> None:
        if not dpg.is_item_shown(self._message):
            return
        text = dpg.get_value(self._message)
        key = (text, self._size)
        if key == self._message_key:
            return
        self._message_key = key
        width = text_width(text, "heading")
        x = max(TEXT_X, (self._size[0] - width) / 2)
        dpg.configure_item(self._message, wrap=max(50, self._size[0] - 2 * TEXT_X))
        dpg.set_item_pos(self._message, [x, max(BAR_HEIGHT + 4, self._size[1] // 2 - 12)])
        self._place_format()

    # --- internals --------------------------------------------------------
    def _show_frame(self, frame) -> None:
        try:
            side = texture_side_for(frame.width, frame.height, *self._image_box())
            rgba = to_display_rgba(frame, side, out=self._buffer)
        except PixelConversionError as exc:
            dpg.set_value(self._message, str(exc))
            dpg.show_item(self._message)
            return

        if rgba is not self._buffer:
            # Size changed (first frame, ROI/format change or tile resized into a new bucket).
            self._drop_texture()
            height, width = rgba.shape[:2]
            self._buffer = rgba
            self._texture = dpg.add_raw_texture(
                width, height, rgba.ravel(), format=dpg.mvFormat_Float_rgba, parent=self._registry
            )
            self._texture_size = (width, height)
            # Insert below the overlay bars and text so they draw on top of the image.
            self._image = dpg.add_image(self._texture, parent=self.tile, before=self._top_bar)
            self._layout_image()
        else:
            dpg.set_value(self._texture, rgba.ravel())

        dpg.hide_item(self._message)
        self._display_frames += 1
        self._last_frame_id = frame.frame_id

    def _image_box(self) -> tuple[int, int]:
        """The whole tile interior: the image is not reduced by the overlay bars."""
        return (max(1, self._size[0] - 2 * INSET), max(1, self._size[1] - 2 * INSET))

    def _layout_image(self) -> None:
        if self._image is None or self._texture_size is None or self._size == (0, 0):
            return
        x, y, w, h = fit_image(*self._texture_size, *self._image_box())
        dpg.configure_item(self._image, width=w, height=h, pos=(INSET + x, INSET + y))

    def _drop_texture(self) -> None:
        # Delete the image before the texture it references.
        if self._image is not None and dpg.does_item_exist(self._image):
            dpg.delete_item(self._image)
        self._image = None
        if self._texture is not None and dpg.does_item_exist(self._texture):
            dpg.delete_item(self._texture)
        self._texture = None
        self._texture_size = None
        self._buffer = None
