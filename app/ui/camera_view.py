"""One multiview tile: live image fitted to the whole tile, with name/state and FPS on overlay bars.

The image always keeps the camera's aspect ratio (no crop, no stretch) and is centred; the tile's
title and statistics are drawn on translucent bars over the image instead of taking their own rows,
so the image can use the full tile height.
"""

from __future__ import annotations

import logging
import time

import dearpygui.dearpygui as dpg
import numpy as np

from app.acquisition.processing import PixelConversionError, to_display_rgba
from app.cameras.camera_manager import CameraManager
from app.models.camera_state import CameraState
from app.models.camera_status import camera_display_name
from app.services.reconnect_service import ReconnectService
from app.services.recording_service import RecordingService
from app.ui.theme import STATE_COLORS, use_font

logger = logging.getLogger(__name__)

BAR_HEIGHT = 26  # overlay bars (top: name + state, bottom: FPS / frame id / errors)
INSET = 1  # keep the image inside the tile border
TEXT_X = 10
OVERLAY_BG = (0, 0, 0, 115)
OVERLAY_TEXT = (240, 240, 245)
OVERLAY_TEXT_DIM = (200, 200, 206)
TILE_ROUNDING = 4
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


_tile_theme: int | None = None
_bar_theme: int | None = None


def _bar_theme_id() -> int:
    """Translucent overlay bar. Child windows honour pos and draw above the parent's image
    (drawlists ignore pos in Dear PyGui, so they cannot be used for the bottom bar)."""
    global _bar_theme
    if _bar_theme is None or not dpg.does_item_exist(_bar_theme):
        with dpg.theme() as _bar_theme:
            with dpg.theme_component(dpg.mvChildWindow):
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, OVERLAY_BG)
                dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 0)
                dpg.add_theme_style(dpg.mvStyleVar_ChildBorderSize, 0)
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 0, 0)
    return _bar_theme


def _tile_theme_id() -> int:
    global _tile_theme
    if _tile_theme is None or not dpg.does_item_exist(_tile_theme):
        with dpg.theme() as _tile_theme:
            with dpg.theme_component(dpg.mvChildWindow):
                dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, TILE_ROUNDING)
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 0, 0)
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (14, 14, 15))  # neutral letterbox
    return _tile_theme


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

        self.tile = dpg.add_child_window(parent=parent, border=True, no_scrollbar=True)
        dpg.bind_item_theme(self.tile, _tile_theme_id())
        # The image is inserted before the bars; the bars (child windows) render above it.
        self._message = dpg.add_text("No camera", parent=self.tile, color=OVERLAY_TEXT_DIM, pos=(TEXT_X, 40))
        self._top_bar = dpg.add_child_window(parent=self.tile, width=1, height=BAR_HEIGHT, pos=(INSET, INSET),
                                             border=False, no_scrollbar=True, no_scroll_with_mouse=True)
        self._bottom_bar = dpg.add_child_window(parent=self.tile, width=1, height=BAR_HEIGHT, pos=(INSET, INSET),
                                                border=False, no_scrollbar=True, no_scroll_with_mouse=True)
        for bar in (self._top_bar, self._bottom_bar):
            dpg.bind_item_theme(bar, _bar_theme_id())
        self._title = dpg.add_text("", parent=self._top_bar, pos=(TEXT_X, 4), color=OVERLAY_TEXT)
        use_font(self._title, "heading")
        self._state = dpg.add_text("", parent=self._top_bar, pos=(TEXT_X, 4))
        self._info = dpg.add_text("", parent=self._bottom_bar, color=OVERLAY_TEXT_DIM, pos=(TEXT_X, 4))

    # --- public -----------------------------------------------------------
    def assign(self, camera_id: str | None, manager: CameraManager) -> None:
        if camera_id == self.camera_id:
            return
        self._drop_texture()
        self.camera_id = camera_id
        self._last_frame_id = None
        if camera_id is None:
            dpg.set_value(self._title, "")
        else:
            cam = manager.camera(camera_id)
            dpg.set_value(self._title, camera_display_name(cam.model, cam.serial_number))
        dpg.set_value(self._message, "No camera" if camera_id is None else "Not streaming")
        dpg.show_item(self._message)

    def set_size(self, width: int, height: int) -> None:
        if (width, height) == self._size:
            return
        self._size = (width, height)
        dpg.configure_item(self.tile, width=width, height=height)
        inner_w = max(1, width - 2 * INSET)
        dpg.configure_item(self._top_bar, width=inner_w, height=BAR_HEIGHT)
        dpg.set_item_pos(self._top_bar, [INSET, INSET])
        dpg.configure_item(self._bottom_bar, width=inner_w, height=BAR_HEIGHT)
        dpg.set_item_pos(self._bottom_bar, [INSET, height - INSET - BAR_HEIGHT])
        dpg.set_item_pos(self._state, [max(TEXT_X, inner_w - 120), 4])
        dpg.set_item_pos(self._message, [TEXT_X, max(BAR_HEIGHT + 8, height // 2 - 10)])
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
        if self.camera_id is None:
            dpg.set_value(self._state, "")
            dpg.set_value(self._info, "")
            return

        state = manager.state(self.camera_id)
        if state is CameraState.ACQUIRING and recording is not None and recording.is_recording(self.camera_id):
            dpg.set_value(self._state, "● REC")
            dpg.configure_item(self._state, color=STATE_COLORS[CameraState.ERROR])
        else:
            dpg.set_value(self._state, f"● {state.value}")
            dpg.configure_item(self._state, color=STATE_COLORS[state])

        frame = manager.latest_frame(self.camera_id)
        if frame is not None:
            self._show_frame(frame)

        now = time.perf_counter()
        elapsed = now - self._display_window_start
        if elapsed >= 1.0:
            self._display_fps = self._display_frames / elapsed
            self._display_frames, self._display_window_start = 0, now

        stats = manager.stats(self.camera_id)
        if stats is None:
            dpg.set_value(self._info, "")
        else:
            frame_id = "-" if stats.last_frame_id is None else stats.last_frame_id
            dpg.set_value(
                self._info,
                f"cam {stats.measured_fps:5.1f} fps   disp {self._display_fps:5.1f} fps   #{frame_id}",
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
            dpg.configure_item(self._info, color=STATE_COLORS[CameraState.ERROR])
            if self._image is None:
                dpg.set_value(self._message, error)
                dpg.show_item(self._message)
        else:
            dpg.configure_item(self._info, color=OVERLAY_TEXT_DIM)

    def delete(self) -> None:
        dpg.delete_item(self.tile)
        self._image = None
        self._drop_texture()

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
