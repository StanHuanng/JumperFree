"""Pure board-level compilation of Clip RGB guidance (hardware unverified)."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from ..core import JfError
from ..routing.model import RouteError
from ..routing.steiner import crossbar_query

RGB = tuple[int, int, int]

# Sixteen clips can form at most eight disjoint nets (two members minimum).
GUIDE_PALETTE = (
    (255, 0, 0),
    (0, 255, 0),
    (0, 0, 255),
    (255, 255, 0),
    (255, 0, 255),
    (0, 255, 255),
    (255, 128, 0),
    (128, 0, 255),
)


class LedPlanError(JfError):
    """A board compilation failure, converted to HostError by the service."""


@dataclass(frozen=True, slots=True)
class LedPlan:
    """Clip colors; brightness_cap is recorded only（待实测）, never applied in V0.1."""

    frames: Mapping[int, RGB]
    brightness_cap: int = 64


def compile_led_plan(
    state: dict[str, Any], palette: Sequence[RGB] = GUIDE_PALETTE,
) -> LedPlan:
    """Assign colors by lexical net ID, without mutation, I/O or brightness scaling."""
    try:
        view = crossbar_query(state)
    except RouteError as exc:
        raise LedPlanError(exc.code, exc.message, **exc.details) from exc
    supported = {f"Clip{clip}" for clip in range(16)}
    unsupported = sorted(set(state["topology"]["node_attachments"]) - supported)
    if unsupported:
        raise LedPlanError(
            "UNSUPPORTED_TOPOLOGY", "guide requires Clip0..Clip15 node IDs",
            nodes=unsupported,
        )
    if len(palette) < len(view["nets"]):
        raise LedPlanError(
            "INVALID_PALETTE", "guide palette must contain at least one color per net",
            required=len(view["nets"]), available=len(palette),
        )
    frames = {
        int(node[4:]): palette[index]
        for index, net in enumerate(view["nets"]) for node in net["nodes"]
    }
    return LedPlan(MappingProxyType(dict(sorted(frames.items()))))


def plan_guide(state: dict[str, Any]) -> list[tuple[int, int, int, int]]:
    """Return the legacy numeric-clip-ordered JSON plan, with original RGB values."""
    return [(clip, *rgb) for clip, rgb in sorted(compile_led_plan(state).frames.items())]
