"""Synthetic V5-scale topology for deterministic software evaluation.

The packaged reference supplies only dimensions and broad chip roles.  The
row placement, special-function endpoints, and two-lane chain below are
explicitly unverified modelling assumptions, not a hardware wiring map.
"""

from __future__ import annotations

import copy
import json
from importlib.resources import files
from typing import Any

from .crossbar import validate_topology


def _reference() -> dict[str, Any]:
    path = files("jumperfree.routing").joinpath(
        "topologies/jumperless_v5_reference.json"
    )
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("packaged V5 reference must contain an object")
    return value


def v5_scale_topology() -> dict[str, Any]:
    """Return the deterministic, synthetic 12-chip scale topology.

    Nothing in this function is a claim about physical V5 wiring.  In
    particular, every generated attachment and chain link is covered by a
    ``verified: false`` metadata record.
    """

    reference = _reference()
    chips = copy.deepcopy(reference["chips"])
    roles = copy.deepcopy(reference["chip_roles"])
    breadboard_chips = list(roles["breadboard"])
    all_chips = [record["name"] for record in chips]

    attachments: dict[str, list[object]] = {}
    row = 1
    for chip_index, chip in enumerate(breadboard_chips):
        count = 8 if chip_index < 4 else 7
        for offset in range(count):
            attachments[f"BB{row:02d}"] = [chip, "X", 8 + offset]
            row += 1
    for chip in roles["special_function"]:
        attachments[f"SF_{chip}"] = [chip, "X", 15]

    links = [
        [[left, "Y", lane if index == 0 else 2 + lane], [right, "Y", lane]]
        for index, (left, right) in enumerate(zip(all_chips, all_chips[1:]))
        for lane in range(2)
    ]
    topology = {
        "chips": chips,
        "node_attachments": attachments,
        "inter_chip_links": links,
        "metadata": {
            "model": "synthetic_v5_scale_simulation",
            "reference_verified": bool(reference.get("verified", False)),
            "chip_roles": roles,
            "hardware_assumptions": [
                {
                    "id": "breadboard_row_attachments",
                    "description": (
                        "BB01-BB60 are placed on X8 and above across chips A-H"
                    ),
                    "verified": False,
                },
                {
                    "id": "special_function_endpoints",
                    "description": "SF_I-SF_L are attached to X15 on chips I-L",
                    "verified": False,
                },
                {
                    "id": "two_lane_chip_chain",
                    "description": (
                        "A-L form a synthetic two-lane chain using Y0/Y1 incoming "
                        "and Y2/Y3 outgoing on internal chips"
                    ),
                    "verified": False,
                },
            ],
        },
    }
    validate_topology(topology)
    return topology
