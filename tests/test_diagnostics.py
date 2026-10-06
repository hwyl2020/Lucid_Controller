import json
import zipfile

from app.cameras.camera_manager import CameraManager
from app.cameras.simulator_camera import SimulatorCamera, SimulatorConfig
from app.services.configuration import DEFAULT_CONFIG
from app.services.diagnostics import export_diagnostics


def test_bundle_contents(tmp_path):
    manager = CameraManager()
    manager.add_camera(SimulatorCamera(SimulatorConfig(camera_id="SIM")))
    manager.connect("SIM")
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "visionx.log").write_text("hello log")
    path = export_diagnostics(tmp_path / "out", manager, DEFAULT_CONFIG, logs)
    manager.shutdown()

    with zipfile.ZipFile(path) as bundle:
        names = set(bundle.namelist())
        assert {"system.json", "network.json", "cameras.json", "config.json", "logs/visionx.log"} <= names
        system = json.loads(bundle.read("system.json"))
        assert system["packages"]["dearpygui"] and system["application_version"]
        cameras = json.loads(bundle.read("cameras.json"))
        assert cameras[0]["camera_id"] == "SIM" and cameras[0]["state"] == "connected"
        assert cameras[0]["settings"]["pixel_format"] == "Mono8"
        assert json.loads(bundle.read("network.json"))["interfaces"]
