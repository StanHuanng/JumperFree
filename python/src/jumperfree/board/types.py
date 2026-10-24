"""Immutable physical switch operations and canonical plan representation."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from ..core import JfError, SCHEMA
from ..core.shadow import _uint


class PlanError(JfError):
    """A structured board/plan failure (no device has been touched)."""


@dataclass(frozen=True, slots=True, order=True)
class Xp:
    chip: int
    x: int
    y: int

    def __post_init__(self) -> None:
        _uint(self.chip, 2, "chip")
        _uint(self.x, 8, "x (V0.1 X8–X15 are unwired)")
        _uint(self.y, 8, "y")

    def bit(self) -> int:
        return self.x * 8 + self.y


@dataclass(frozen=True, slots=True)
class SwitchOp:
    xp: Xp
    on: bool
    tag: int

    def __post_init__(self) -> None:
        if not isinstance(self.xp, Xp) or type(self.on) is not bool:
            raise PlanError("BAD_ARG", "operation requires Xp and boolean on")
        _uint(self.tag, 16, "tag")

    def to_json(self) -> dict[str, object]:
        return {"chip": self.xp.chip, "x": self.xp.x, "y": self.xp.y, "on": self.on, "tag": self.tag}


@dataclass(frozen=True, slots=True)
class SwitchPlan:
    schema: str
    base_epoch: int
    topology_id: str
    breaks: tuple[SwitchOp, ...]
    makes: tuple[SwitchOp, ...]
    expect_crc: int
    disturbed_nets: tuple[str, ...]
    tags: Mapping[str, int]

    def __post_init__(self) -> None:
        if self.schema != SCHEMA["plan"]:
            raise PlanError("BAD_ARG", "unsupported switch plan schema")
        _uint(self.base_epoch, 1 << 64, "base_epoch")
        _uint(self.expect_crc, 1 << 32, "expect_crc")
        object.__setattr__(self, "breaks", tuple(sorted(self.breaks, key=lambda op: op.xp)))
        object.__setattr__(self, "makes", tuple(sorted(self.makes, key=lambda op: op.xp)))
        object.__setattr__(self, "disturbed_nets", tuple(sorted(set(self.disturbed_nets))))
        object.__setattr__(self, "tags", MappingProxyType(dict(sorted(self.tags.items()))))

    def to_wire_tokens(self) -> list[str]:
        return [f"-{op.xp.chip}:{op.xp.x}:{op.xp.y}" for op in self.breaks] + [
            f"+{op.xp.chip}:{op.xp.x}:{op.xp.y}@{op.tag}" for op in self.makes
        ]

    def to_json(self) -> dict[str, object]:
        return {
            "schema": self.schema, "base_epoch": self.base_epoch, "topology_id": self.topology_id,
            "breaks": [op.to_json() for op in self.breaks],
            "makes": [op.to_json() for op in self.makes], "expect_crc": self.expect_crc,
            "disturbed_nets": list(self.disturbed_nets), "tags": dict(self.tags),
        }
