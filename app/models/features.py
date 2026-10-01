"""Application-level view of a camera's GenICam feature tree (no SDK objects).

Backends (ArenaCamera, SimulatorCamera) translate their node maps into these plain dataclasses, so
the Property Grid UI can browse and edit any camera without touching Arena objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class FeatureKind(Enum):
    INTEGER = "Integer"
    FLOAT = "Float"
    BOOLEAN = "Boolean"
    ENUMERATION = "Enumeration"
    STRING = "String"
    COMMAND = "Command"
    REGISTER = "Register"


class Visibility(Enum):
    """GenICam visibility levels, lowest first."""

    BEGINNER = 0
    EXPERT = 1
    GURU = 2
    INVISIBLE = 3


@dataclass(frozen=True)
class Feature:
    name: str
    display_name: str
    kind: FeatureKind
    access: str  # GenICam access mode: "RW", "RO", "WO", "NA" (not available now), "NI" (not implemented)
    visibility: Visibility = Visibility.BEGINNER
    value: object = None  # None when unreadable or not applicable (command/register)
    minimum: float | None = None
    maximum: float | None = None
    increment: float | None = None
    unit: str = ""
    entries: tuple[str, ...] = ()  # enumeration entries currently available
    description: str = ""
    error: str | None = None  # reading this node failed

    @property
    def implemented(self) -> bool:
        return self.access != "NI"

    @property
    def available(self) -> bool:
        return self.access not in ("NA", "NI")

    @property
    def readable(self) -> bool:
        return self.access in ("RO", "RW")

    @property
    def writable(self) -> bool:
        return self.access in ("RW", "WO")


@dataclass(frozen=True)
class FeatureCategory:
    name: str
    display_name: str
    features: tuple[Feature, ...] = ()
    subcategories: tuple[FeatureCategory, ...] = field(default=())

    def walk(self):
        """All features in this category and below, depth first."""
        yield from self.features
        for sub in self.subcategories:
            yield from sub.walk()

    def find(self, name: str) -> Feature | None:
        return next((f for f in self.walk() if f.name == name), None)
