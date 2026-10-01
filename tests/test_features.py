"""Property Grid backend: ArenaCamera feature tree via a fake GenICam node map, simulator features,
FeatureService and search."""

from types import SimpleNamespace

import pytest

from app.cameras.arena_camera import ArenaCamera
from app.cameras.camera_device import (
    CameraNotConnectedError,
    InvalidStateError,
    InvalidValueError,
    UnsupportedFeatureError,
)
from app.cameras.camera_discovery import ArenaDeviceInfo
from app.cameras.camera_manager import CameraManager
from app.cameras.simulator_camera import SimulatorCamera, SimulatorConfig
from app.models.features import FeatureKind, Visibility
from app.services.feature_service import FeatureService, matches
from tests import fake_arena


def _named(name):
    return SimpleNamespace(name=name)


class GenICamNode:
    """Mimics the arena_api node attributes the backend reads (names as in arena_api 2.7.1)."""

    def __init__(self, name, kind, access="RW", value=None, visibility="BEGINNER", display=None, tip="",
                 min=None, max=None, inc=None, unit="", entries=None, children=None, fail=False):
        self.name, self.display_name, self.tool_tip, self.description = name, display or name, tip, ""
        self.interface_type, self.access_mode, self.visibility = _named(kind), _named(access), _named(visibility)
        self._value, self.min, self.max, self.inc, self.unit = value, min, max, inc, unit
        self.enumentry_nodes = {n: SimpleNamespace(is_readable=r) for n, r in (entries or {}).items()}
        self.features = {c.name: c for c in (children or [])}
        self.fail, self.executed, self.writes = fail, 0, []

    @property
    def is_readable(self):
        return self.access_mode.name in ("RO", "RW")

    @property
    def is_writable(self):
        return self.access_mode.name in ("RW", "WO")

    @property
    def value(self):
        if self.fail:
            raise Exception("Arena ERROR : ... ArenaC ERROR : IO -1010")
        return self._value

    @value.setter
    def value(self, v):
        self.writes.append(v)
        self._value = v

    def execute(self):
        self.executed += 1


def build_nodemap():
    leaves = {
        "DeviceSerialNumber": GenICamNode("DeviceSerialNumber", "STRING", "RO", "263401242", display="Serial Number"),
        "DeviceUserID": GenICamNode("DeviceUserID", "STRING", "RW", ""),
        "ExposureTime": GenICamNode("ExposureTime", "FLOAT", "RW", 5000.0, min=20.0, max=1e6, unit="us",
                                    tip="Exposure time in microseconds"),
        "ExposureAuto": GenICamNode("ExposureAuto", "ENUMERATION", "RW", "Off",
                                    entries={"Off": True, "Once": True, "Continuous": True, "Hidden": False}),
        "Width": GenICamNode("Width", "INTEGER", "RO", 4024, min=4, max=4024, inc=4),
        "OffsetX": GenICamNode("OffsetX", "INTEGER", "RW", 0, min=0, max=100, inc=4, visibility="EXPERT"),
        "ReverseX": GenICamNode("ReverseX", "BOOLEAN", "RW", False),
        "TriggerSoftware": GenICamNode("TriggerSoftware", "COMMAND", "WO", visibility="EXPERT"),
        "TriggerDelay": GenICamNode("TriggerDelay", "FLOAT", "NA"),
        "BrokenNode": GenICamNode("BrokenNode", "INTEGER", "RO", fail=True),
        "LUT": GenICamNode("LUT", "REGISTER", "RW", visibility="GURU"),
    }
    entry = GenICamNode("ExposureAuto_Off", "ENUMENTRY")
    device = GenICamNode("DeviceControl", "CATEGORY", children=[leaves["DeviceSerialNumber"], leaves["DeviceUserID"]],
                         display="Device Control")
    analog = GenICamNode("AnalogControl", "CATEGORY", children=[leaves["ExposureTime"], leaves["ExposureAuto"], entry])
    image = GenICamNode("ImageFormatControl", "CATEGORY",
                        children=[leaves["Width"], leaves["OffsetX"], leaves["ReverseX"], leaves["BrokenNode"]])
    trigger = GenICamNode("TriggerControl", "CATEGORY",
                          children=[leaves["TriggerSoftware"], leaves["TriggerDelay"], leaves["LUT"]])
    acquisition = GenICamNode("AcquisitionControl", "CATEGORY", children=[trigger])
    root = GenICamNode("Root", "CATEGORY", children=[device, acquisition, analog, image])
    nodes = {**leaves, "Root": root, "DeviceControl": device}
    return nodes


@pytest.fixture
def arena_cam(monkeypatch):
    sdk = fake_arena.install(monkeypatch)
    cam = ArenaCamera(ArenaDeviceInfo.from_sdk(fake_arena.device_info()))
    cam.connect()
    nodes = build_nodemap()
    device = next(iter(sdk.system.devices.values()))
    device.nodemap.nodes.update(nodes)
    yield cam, nodes
    cam.disconnect()


# --- ArenaCamera ---------------------------------------------------------------
def test_tree_follows_camera_categories(arena_cam):
    cam, _ = arena_cam
    tree = cam.feature_tree()
    assert [c.name for c in tree.subcategories] == ["DeviceControl", "AcquisitionControl", "AnalogControl",
                                                   "ImageFormatControl"]
    assert tree.subcategories[0].display_name == "Device Control"
    assert [c.name for c in tree.subcategories[1].subcategories] == ["TriggerControl"]  # nested category
    names = [f.name for f in tree.walk()]
    assert "ExposureAuto_Off" not in names  # enum entries are not features


def test_feature_kinds_values_and_limits(arena_cam):
    tree = arena_cam[0].feature_tree()
    exposure = tree.find("ExposureTime")
    assert (exposure.kind, exposure.value, exposure.minimum, exposure.maximum, exposure.unit) == (
        FeatureKind.FLOAT, 5000.0, 20.0, 1e6, "us")
    assert exposure.writable and exposure.description == "Exposure time in microseconds"
    auto = tree.find("ExposureAuto")
    assert auto.kind is FeatureKind.ENUMERATION and auto.entries == ("Off", "Once", "Continuous")
    serial = tree.find("DeviceSerialNumber")
    assert serial.readable and not serial.writable and serial.display_name == "Serial Number"
    assert tree.find("Width").increment == 4
    assert tree.find("OffsetX").visibility is Visibility.EXPERT
    assert tree.find("ReverseX").kind is FeatureKind.BOOLEAN
    command = tree.find("TriggerSoftware")
    assert command.kind is FeatureKind.COMMAND and command.value is None and command.writable


def test_unavailable_and_broken_nodes_do_not_break_the_tree(arena_cam):
    tree = arena_cam[0].feature_tree()
    delay = tree.find("TriggerDelay")
    assert not delay.available and delay.value is None
    broken = tree.find("BrokenNode")
    assert broken.error and "IO" in broken.error and broken.value is None


def test_write_validates_against_live_limits(arena_cam):
    cam, nodes = arena_cam
    cam.write_feature("ExposureTime", "7500")
    assert nodes["ExposureTime"].writes == [7500.0]
    cam.write_feature("OffsetX", 8)
    with pytest.raises(InvalidValueError):
        cam.write_feature("OffsetX", 6)  # not on the increment
    with pytest.raises(InvalidValueError):
        cam.write_feature("ExposureTime", 5)  # below minimum
    with pytest.raises(InvalidValueError):
        cam.write_feature("ExposureAuto", "Hidden")  # entry not currently available
    with pytest.raises(InvalidValueError):
        cam.write_feature("ExposureTime", "abc")
    cam.write_feature("ReverseX", True)
    assert nodes["ReverseX"].writes == [True]


def test_read_only_and_missing_features(arena_cam):
    cam, _ = arena_cam
    with pytest.raises(InvalidStateError, match="read-only"):
        cam.write_feature("Width", 1000)
    cam.start_acquisition()
    with pytest.raises(InvalidStateError, match="stop acquisition"):
        cam.write_feature("Width", 1000)
    with pytest.raises(UnsupportedFeatureError):
        cam.write_feature("NoSuchNode", 1)


def test_execute_command(arena_cam):
    cam, nodes = arena_cam
    cam.execute_feature("TriggerSoftware")
    assert nodes["TriggerSoftware"].executed == 1
    with pytest.raises(UnsupportedFeatureError):
        cam.execute_feature("ExposureTime")


def test_no_root_category_means_no_tree(monkeypatch):
    fake_arena.install(monkeypatch)
    cam = ArenaCamera(ArenaDeviceInfo.from_sdk(fake_arena.device_info()))
    cam.connect()
    assert cam.feature_tree() is None


# --- simulator + service ---------------------------------------------------------
@pytest.fixture
def service():
    manager = CameraManager(frame_timeout=0.1)
    manager.add_camera(SimulatorCamera(SimulatorConfig(camera_id="A", width=640, height=480)))
    manager.add_camera(SimulatorCamera(SimulatorConfig(camera_id="B", width=320, height=240)))
    yield manager, FeatureService(manager)
    manager.shutdown()


def test_service_requires_connection(service):
    _, svc = service
    with pytest.raises(CameraNotConnectedError):
        svc.tree("A")


def test_trees_are_camera_specific(service):
    manager, svc = service
    manager.connect("A")
    manager.connect("B")
    svc.write("A", "ExposureTime", 1234)
    tree_a, tree_b = svc.tree("A"), svc.tree("B")
    assert tree_a.find("ExposureTime").value == 1234
    assert tree_b.find("ExposureTime").value != 1234
    assert tree_a.find("SensorWidth").value == 640 and tree_b.find("SensorWidth").value == 320


def test_simulator_locks_format_while_acquiring(service):
    manager, svc = service
    manager.connect("A")
    assert svc.tree("A").find("PixelFormat").writable
    manager.start_streaming("A")
    import time
    time.sleep(0.2)
    assert svc.tree("A").find("PixelFormat").access == "RO"
    with pytest.raises(InvalidStateError):
        svc.write("A", "Width", 320)


def test_simulator_writes_and_commands(service):
    manager, svc = service
    manager.connect("A")
    svc.write("A", "Width", 320)
    svc.write("A", "DeviceUserID", "line-3")
    svc.execute("A", "TriggerSoftware")
    tree = svc.tree("A")
    assert tree.find("Width").value == 320 and tree.find("DeviceUserID").value == "line-3"
    with pytest.raises(InvalidValueError):
        svc.write("A", "DeviceUserID", "x" * 17)
    with pytest.raises(UnsupportedFeatureError):
        svc.write("A", "DeviceSerialNumber", "1")


def test_search_matches_name_display_name_and_visibility(arena_cam):
    tree = arena_cam[0].feature_tree()
    found = [f.name for f in tree.walk() if matches(f, "exposure")]
    assert found == ["ExposureTime", "ExposureAuto"]
    assert [f.name for f in tree.walk() if matches(f, "serial number")] == ["DeviceSerialNumber"]
    beginner = [f.name for f in tree.walk() if matches(f, "", Visibility.BEGINNER)]
    assert "OffsetX" not in beginner and "ExposureTime" in beginner
