"""Pure planning of Clip RGB guidance from a software crossbar state."""

from __future__ import annotations

from typing import Any

from ..routing.model import RouteError
from ..routing.steiner import crossbar_query
from .errors import HostError
from .protocol import CLIP_COUNT

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


def plan_guide(state: dict[str, Any]) -> list[tuple[int, int, int, int]]:
    """Return numeric-clip-ordered ``(clip, r, g, b)`` entries without I/O.

    Net IDs in lexical order receive successive fixed palette colors. Only
    canonical Clip0..Clip15 node IDs are supported, including unused nodes.
    Invalid routing states are reported as structured HostError failures.
    """
    try:
        view = crossbar_query(state)
    except RouteError as exc:
        raise HostError(exc.code, exc.message, **exc.details) from exc
    supported = {f"Clip{clip}" for clip in range(CLIP_COUNT)}
    unsupported = sorted(set(state["topology"]["node_attachments"]) - supported)
    if unsupported:
        raise HostError(
            "UNSUPPORTED_TOPOLOGY", "guide requires Clip0..Clip15 node IDs",
            nodes=unsupported,
        )
    plan = []
    for index, net in enumerate(view["nets"]):
        rgb = GUIDE_PALETTE[index]
        plan.extend((int(node[4:]), *rgb) for node in net["nodes"])
    return sorted(plan)
