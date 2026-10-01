"""One multiview tile: live image plus camera name, state and FPS overlay."""

from __future__ import annotations

import logging
import time

import dearpygui.dearpygui as dpg
import numpy as np

from app.acquisition.processing import PixelConversionError, to_display_rgba
from app.cameras.camera_manager import CameraManager
from app.services.reconnect_service import ReconnectService
from app.services.recording_service import RecordingService
from app.models.camera_state import CameraState
from app.models.camera_status import camera_display_name
from app.ui.theme import STATE_COLORS, TEXT_DIM, use_font

logger = logging.getLogger(__name__)

HEADER_HEIGHT = 28
FOOTER_HEIGHT = 24
PADDING = 6
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
        self._title = dpg.add_text("", parent=self.tile, pos=(10, 6))
        use_font(self._title, "heading")
        self._state = dpg.add_text("", parent=self.tile, pos=(10, 6))
        self._message = dpg.add_text("No camera", parent=self.tile, color=TEXT_DIM, pos=(10, 40))
        self._info = dpg.add_text("", parent=self.tile, color=TEXT_DIM, pos=(10, 6))

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
        dpg.configure_item(self._state, pos=(max(10, width - 120), 6))
        dpg.configure_item(self._info, pos=(10, height - FOOTER_HEIGHT))
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
            # Keep the last image visible; report the error in the footer (or centre if no image).
            dpg.set_value(self._info, error)
            dpg.configure_item(self._info, color=STATE_COLORS[CameraState.ERROR])
            if self._image is None:
                dpg.set_value(self._message, error)
                dpg.show_item(self._message)
        else:
            dpg.configure_item(self._info, color=TEXT_DIM)

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
            # Insert before the text items so the overlay draws on top of the image.
            self._image = dpg.add_image(self._texture, parent=self.tile, before=self._title)
            self._layout_image()
        else:
            dpg.set_value(self._texture, rgba.ravel())

        dpg.hide_item(self._message)
        self._display_frames += 1
        self._last_frame_id = frame.frame_id

    def _image_box(self) -> tuple[int, int]:
        """Space available for the image inside the tile (between title and footer)."""
        return (self._size[0] - 2 * PADDING, self._size[1] - HEADER_HEIGHT - FOOTER_HEIGHT - 2 * PADDING)

    def _layout_image(self) -> None:
        if self._image is None or self._texture_size is None or self._size == (0, 0):
            return
        tex_w, tex_h = self._texture_size
        avail_w, avail_h = self._image_box()
        scale = max(min(avail_w / tex_w, avail_h / tex_h), 0.01)
        w, h = max(1, int(tex_w * scale)), max(1, int(tex_h * scale))
        x = PADDING + (avail_w - w) // 2
        y = HEADER_HEIGHT + PADDING + (avail_h - h) // 2
        dpg.configure_item(self._image, width=w, height=h, pos=(x, y))

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
