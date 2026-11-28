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


# JumperFree mini: one color per JF2 tag (1..15), so a net keeps its color while
# other nets are added or removed. Saturated hues, distinct at low LED level.
TAG_PALETTE = (
    (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 160, 0), (0, 255, 255),
    (255, 0, 255), (255, 255, 0), (255, 255, 255), (128, 0, 255), (0, 128, 255),
    (255, 64, 128), (128, 255, 0), (255, 96, 0), (0, 255, 128), (96, 96, 255),
)


def compile_led_plan(
    state: dict[str, Any], palette: Sequence[RGB] = GUIDE_PALETTE,
    led_map: Mapping[str, int] | None = None, tags: Mapping[str, int] | None = None,
) -> LedPlan:
    """Assign colors without mutation, I/O or brightness scaling.

    v01 (no ``led_map``): lexical net order, nodes must be Clip0..Clip15.
    With ``led_map`` (node -> LED index) nodes without an LED (service nodes) stay
    dark; with ``tags`` (net -> 1..15) the color is ``palette[tag - 1]``.
    """
    try:
        view = crossbar_query(state)
    except RouteError as exc:
        raise LedPlanError(exc.code, exc.message, **exc.details) from exc
    if led_map is None:
        supported = {f"Clip{clip}" for clip in range(16)}
        unsupported = sorted(set(state["topology"]["node_attachments"]) - supported)
        if unsupported:
            raise LedPlanError(
                "UNSUPPORTED_TOPOLOGY", "guide requires Clip0..Clip15 node IDs",
                nodes=unsupported,
            )
        led_map = {name: int(name[4:]) for name in supported}
    needed = max(tags.values(), default=0) if tags is not None else len(view["nets"])
    if len(palette) < needed:
        raise LedPlanError(
            "INVALID_PALETTE", "guide palette must contain at least one color per net",
            required=needed, available=len(palette),
        )
    frames = {
        led_map[node]: palette[(tags[net["net"]] - 1) if tags is not None else index]
        for index, net in enumerate(view["nets"]) for node in net["nodes"] if node in led_map
    }
    return LedPlan(MappingProxyType(dict(sorted(frames.items()))))


def plan_guide(state: dict[str, Any]) -> list[tuple[int, int, int, int]]:
    """Return the legacy numeric-clip-ordered JSON plan, with original RGB values."""
    return [(clip, *rgb) for clip, rgb in sorted(compile_led_plan(state).frames.items())]
