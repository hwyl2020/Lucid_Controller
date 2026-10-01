from enum import Enum


class CameraState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    ACQUIRING = "acquiring"
    ERROR = "error"
