"""Minimal in-memory stand-in for the parts of arena_api used by ArenaCamera.

Shapes and behaviours mirror arena_api 2.7.1: get_node raises ValueError for unknown names,
get_buffer takes an int millisecond timeout and raises the builtin TimeoutError, buffer.pdata is a
ctypes POINTER(c_uint8), pixel_format is an enum member.
"""

from __future__ import annotations

import ctypes
import enum
from collections import deque
from types import SimpleNamespace

import numpy as np

from app.cameras import arena_sdk


class PixelFormat(enum.IntEnum):
    Mono8 = 1
    Mono12 = 2
    RGB8 = 3
    BayerRG8 = 4
    Mono10p = 5


fake_enums = SimpleNamespace(PixelFormat=PixelFormat)


class FakeNode:
    def __init__(self, value=None, min=None, max=None, inc=None, readable=True, writable=True, entries=None):
        self.value = value
        self.min, self.max, self.inc = min, max, inc
        self.is_readable, self.is_writable = readable, writable
        self.writes: list = []
        if entries is not None:  # enumeration: {name: readable}
            self.enumentry_nodes = {n: SimpleNamespace(is_readable=r) for n, r in entries.items()}

    def __setattr__(self, key, value):
        if key == "value" and "writes" in self.__dict__:
            self.writes.append(value)
        super().__setattr__(key, value)


class FakeNodemap:
    def __init__(self, nodes: dict[str, FakeNode], write_log: list | None = None):
        self.nodes = nodes

    def get_node(self, name):
        if name not in self.nodes:
            raise ValueError(f"'{name}' node does not exist in this nodemap")
        return self.nodes[name]


class FakeBuffer:
    def __init__(self, data: np.ndarray, pixel_format: PixelFormat, frame_id: int,
                 bits_per_pixel: int, padding_x: int = 0, incomplete: bool = False):
        height, width = data.shape[:2]
        row = data.reshape(height, -1).view(np.uint8)
        if padding_x:
            row = np.hstack([row, np.full((height, padding_x), 0xEE, np.uint8)])
        self._memory = np.array(row, copy=True).ravel()  # own memory, like the SDK's buffer
        self.pdata = self._memory.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))
        self.width, self.height = width, height
        self.padding_x = padding_x
        self.bits_per_pixel = bits_per_pixel
        self.pixel_format = pixel_format
        self.frame_id = frame_id
        self.is_incomplete = incomplete

    def scribble(self):
        """Simulate the SDK reusing the memory after requeue."""
        self._memory[:] = 0x55


class FakeDevice:
    def __init__(self, nodes=None):
        self.nodemap = FakeNodemap(nodes if nodes is not None else default_nodes())
        self.tl_stream_nodemap = FakeNodemap({
            "StreamBufferHandlingMode": FakeNode("NewestOnly"),
            "StreamAutoNegotiatePacketSize": FakeNode(False),
            "StreamPacketResendEnable": FakeNode(False),
        })
        self.buffers: deque = deque()
        self.requeued: list = []
        self.streaming = False
        self.connected = True
        self.start_error: Exception | None = None

    def is_connected(self):
        return self.connected

    def start_stream(self, number_of_buffers=None):
        if self.start_error:
            raise self.start_error
        self.streaming = True

    def stop_stream(self):
        self.streaming = False

    def get_buffer(self, number_of_buffers=1, timeout=None):
        assert isinstance(timeout, int), "arena_api requires an int millisecond timeout"
        if not self.buffers:
            raise TimeoutError("Arena ERROR : TIMEOUT -1011")
        return self.buffers.popleft()

    def requeue_buffer(self, buffer):
        self.requeued.append(buffer)
        buffer.scribble()


class FakeSystem:
    def __init__(self, infos=None):
        self.infos = infos if infos is not None else [device_info()]
        self.devices: dict[str, FakeDevice] = {}
        self.destroyed: list = []
        self.create_error: Exception | None = None
        self.DEVICE_INFOS_TIMEOUT_MILLISEC = 100

    @property
    def device_infos(self):
        return [dict(i) for i in self.infos]

    def create_device(self, device_infos=None):
        if self.create_error:
            raise self.create_error
        mac = device_infos["mac"]
        device = self.devices.setdefault(mac, FakeDevice())
        return [device]

    def destroy_device(self, device=None):
        self.destroyed.append(device)

    # Host adapters (system.interface_infos) and GigE ForceIP (system.force_ip), as in arena_api 2.7.1.
    interface_infos = [{"ip": "169.254.1.1", "subnetmask": "255.255.0.0", "mac": "e0:00:00:00:00:01"}]
    ignore_force_ip = False
    forced: list = []

    def force_ip(self, device_info):
        self.forced = [*self.forced, dict(device_info)]
        if self.ignore_force_ip:
            return
        for info in self.infos:
            if info["mac"] == device_info["mac"]:
                info["ip"] = device_info["ip"]
                info["subnetmask"] = device_info["subnetmask"]
                info["defaultgateway"] = device_info["defaultgateway"]


class FakeBufferFactory:
    def __init__(self):
        self.converted: list = []
        self.destroyed: list = []

    def convert(self, buffer, new_pixel_format, bayer_algorithm=None):
        mono = np.ctypeslib.as_array(buffer.pdata, shape=(buffer.height * buffer.width,))
        mono = mono.reshape(buffer.height, buffer.width).copy()
        if new_pixel_format.name == "Mono8":
            out = FakeBuffer(mono, PixelFormat.Mono8, buffer.frame_id, 8)
        else:
            out = FakeBuffer(np.repeat(mono[:, :, None], 3, axis=2), PixelFormat[new_pixel_format.name], buffer.frame_id, 24)
        self.converted.append(out)
        return out

    def destroy(self, buffer):
        self.destroyed.append(buffer)


def device_info(serial="224500001", mac="1c:0f:af:00:00:01", model="TRI051S-C", ip="169.254.1.10"):
    return {"model": model, "vendor": "Lucid Vision Labs", "serial": serial, "ip": ip,
            "subnetmask": "255.255.0.0", "defaultgateway": "0.0.0.0", "mac": mac, "name": "",
            "version": "1.80.0.0", "dhcp": False, "persistentip": False, "lla": True}


def default_nodes() -> dict[str, FakeNode]:
    return {
        "AcquisitionMode": FakeNode("SingleFrame"),
        "ExposureAuto": FakeNode("Continuous"),
        "ExposureTime": FakeNode(5000.0, min=20.0, max=2_000_000.0, inc=None),
        "GainAuto": FakeNode("Continuous"),
        "Gain": FakeNode(0.0, min=0.0, max=48.0, inc=None),
        "AcquisitionFrameRateEnable": FakeNode(False),
        "AcquisitionFrameRate": FakeNode(24.0, min=1.0, max=24.6, inc=None),
        "PixelFormat": FakeNode("BayerRG8", entries={"Mono8": True, "BayerRG8": True, "RGB8": True, "BayerRG16": False}),
        "Width": FakeNode(2448, min=64, max=2448, inc=8),
        "Height": FakeNode(2048, min=2, max=2048, inc=2),
        "WidthMax": FakeNode(2448, writable=False),
        "HeightMax": FakeNode(2048, writable=False),
        "OffsetX": FakeNode(0, min=0, max=0, inc=8),
        "OffsetY": FakeNode(0, min=0, max=0, inc=2),
    }


def install(monkeypatch, system: FakeSystem | None = None) -> SimpleNamespace:
    system = system or FakeSystem()
    factory = FakeBufferFactory()
    monkeypatch.setattr(arena_sdk, "_sdk", arena_sdk.ArenaSdk(system=system, buffer_factory=factory, enums=fake_enums))
    return SimpleNamespace(system=system, factory=factory)
