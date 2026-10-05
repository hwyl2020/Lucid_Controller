"""Resizable panes: Splitter clamping and the sections' user-set heights."""

import dearpygui.dearpygui as dpg
import pytest

from app.cameras.camera_manager import CameraManager
from app.cameras.simulator_camera import SimulatorCamera, SimulatorConfig
from app.services.camera_status_service import CameraStatusService
from app.services.log_buffer import LogBuffer
from app.ui.log_panel import LogPanel
from app.ui.status_panel import MIN_BODY_HEIGHT, StatusPanel
from app.ui.widgets import Splitter


@pytest.fixture
def ctx():
    dpg.create_context()
    with dpg.window() as window:
        pass
    yield window
    dpg.destroy_context()


def test_splitter_clamps_to_its_limits(ctx):
    size = {"v": 300}
    splitter = Splitter(ctx, vertical=True, get_size=lambda: size["v"], set_size=lambda v: size.update(v=v),
                        minimum=200, maximum=lambda: 500)
    assert (splitter.clamp(100), splitter.clamp(350.4), splitter.clamp(900)) == (200, 350, 500)
    splitter.update()  # not being dragged: nothing changes
    assert size["v"] == 300 and not splitter.dragging


def test_status_panel_keeps_a_dragged_height_and_can_refit(ctx):
    manager = CameraManager()
    manager.add_camera(SimulatorCamera(SimulatorConfig(camera_id="A")))
    panel = StatusPanel(ctx, CameraStatusService(manager), 5, height=150)
    panel.update()
    assert panel.body_height() == 150  # the saved height wins over fitting to the rows
    panel.set_body_height(MIN_BODY_HEIGHT + 40)
    panel.update()
    assert panel.body_height() == MIN_BODY_HEIGHT + 40
    panel.fit_to_rows()
    panel.update()
    assert panel.body_height() != MIN_BODY_HEIGHT + 40


def test_log_panel_height_from_config(ctx):
    panel = LogPanel(ctx, LogBuffer(), 5, default_open=True, height=260)
    assert panel.body_height() == 260
    panel.set_body_height(120)
    assert panel.body_height() == 120
