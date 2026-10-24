"""Unverified JumperFree V0.1 software topology, without hardware switching."""

from __future__ import annotations

from typing import Any

from .crossbar import validate_topology


def v01_topology() -> dict[str, Any]:
    """Return a fresh, validated two-chip topology with eight shared Y channels.

    Clip0-7 attach to C1 X0-X7; Clip8-15 attach to C2 X0-X7.  X8-X15
    have no external wiring.  All dimensions and wiring are unverified design
    assumptions for JumperFree V0.1, not measured hardware properties.
    """

    topology = {
        "chips": [
            {"name": name, "x_lines": 16, "y_lines": 8}
            for name in ("C1", "C2")
        ],
        "node_attachments": {
            f"Clip{chip_index * 8 + line}": [name, "X", line]
            for chip_index, name in enumerate(("C1", "C2"))
            for line in range(8)
        },
        "inter_chip_links": [
            [["C1", "Y", line], ["C2", "Y", line]]
            for line in range(8)
        ],
        "metadata": {
            "model": "jumperfree_v01_software_topology",
            "verified": False,
            "hardware_assumptions": [
                {
                    "id": "two_ch446q_style_chips",
                    "description": "C1 and C2 each have 16 X lines and 8 Y lines",
                    "verified": False,
                },
                {
                    "id": "clip_attachments",
                    "description": (
                        "Clip0-7 attach to C1 X0-X7; Clip8-15 attach to C2 X0-X7"
                    ),
                    "verified": False,
                },
                {
                    "id": "unused_upper_x_lines",
                    "description": "X8-X15 on both chips have no external wiring",
                    "verified": False,
                },
                {
                    "id": "eight_shared_y_channels",
                    "description": (
                        "C1 Y0-Y7 are fixed-wired to C2 Y0-Y7 respectively, "
                        "forming eight independent shared channels"
                    ),
                    "verified": False,
                },
            ],
        },
    }
    validate_topology(topology)
    return topology
