"""JumperFree mini (PCB v0.4) routing topology and node table.

Seven CH446Q chips U301..U307. X0..X15 of all chips are wired together as
BUS0..BUS15. Each Y line of U301..U306 is one clip (FLIP n = chip n//8, Y n%8).
U307 is the service chip: Y6 = TEST_SRC (PB8 through 1.5 kOhm, signal level
~3.3 V), Y7 = SVC_GND (R410 to ground). U307 Y0..Y5 carry measurement paths and
are reserved: they get internal nodes so the router never uses them.

Clip names follow Stan's row naming: row A = back row (far from the user, USB
side, firmware "U"), row B = front row (near the user, firmware "D"), column
1..24 from the left. Source: firmware/boards/mini-f103 clipmap.h
(EDA pins of PCB v04). LED BATCH clip index = FLIP number.
"""

from __future__ import annotations

from typing import Any

from .crossbar import validate_topology

CHIPS = tuple(f"U30{index}" for index in range(1, 8))
SERVICE_CHIP = "U307"

# FLIP n -> (firmware row, column); index = FLIP number.
_CLIPMAP = (
    ("U", 24), ("U", 23), ("U", 22), ("U", 21), ("U", 17), ("U", 18), ("U", 19), ("U", 20),
    ("U", 16), ("U", 15), ("U", 14), ("U", 13), ("U", 9), ("U", 10), ("U", 11), ("U", 12),
    ("U", 8), ("U", 7), ("U", 6), ("U", 5), ("U", 1), ("U", 2), ("U", 3), ("U", 4),
    ("D", 17), ("D", 18), ("D", 19), ("D", 20), ("D", 24), ("D", 23), ("D", 22), ("D", 21),
    ("D", 9), ("D", 10), ("D", 11), ("D", 12), ("D", 16), ("D", 15), ("D", 14), ("D", 13),
    ("D", 1), ("D", 2), ("D", 3), ("D", 4), ("D", 8), ("D", 7), ("D", 6), ("D", 5),
)
_ROW = {"U": "A", "D": "B"}

SERVICE_NODES = {
    "TEST_SRC": (6, "Weak 3.3 V test source: PB8 through 1.5 kOhm. Signal level only (pull-up), "
                    "never a power supply. Must not share a net with SVC_GND."),
    "SVC_GND": (7, "Ground through the service switch (R410 = 0 Ohm on this board). "
                   "Small currents only: one switch carries at most 15 mA. Never tie it to a driven output."),
}


def mini_nodes() -> dict[str, dict[str, Any]]:
    """Return the node table: clips (with row/column/LED), service and reserved nodes."""
    nodes: dict[str, dict[str, Any]] = {}
    for flip, (row, col) in enumerate(_CLIPMAP):
        name = f"{_ROW[row]}{col}"
        nodes[name] = {"kind": "clip", "row": _ROW[row], "col": col, "flip": flip, "led": flip,
                       "chip": CHIPS[flip // 8], "y": flip % 8}
    for name, (y, note) in SERVICE_NODES.items():
        nodes[name] = {"kind": "service", "chip": SERVICE_CHIP, "y": y, "note": note}
    for y in range(6):
        nodes[f"~{SERVICE_CHIP}.Y{y}"] = {"kind": "reserved", "chip": SERVICE_CHIP, "y": y}
    return dict(sorted(nodes.items()))


def mini_topology() -> dict[str, Any]:
    """Return a fresh, validated topology: per-chip Y attachments, BUS links across chips."""
    topology = {
        "chips": [{"name": name, "x_lines": 16, "y_lines": 8} for name in CHIPS],
        "node_attachments": {
            name: [node["chip"], "Y", node["y"]] for name, node in mini_nodes().items()
        },
        "inter_chip_links": [
            [[CHIPS[index], "X", bus], [CHIPS[index + 1], "X", bus]]
            for bus in range(16) for index in range(len(CHIPS) - 1)
        ],
        "metadata": {
            "model": "jumperfree_mini_topology",
            "board": "JumperFree mini (PCB v0.4, firmware board=mini)",
            "verified": "bench",
            "evidence": ("2026-11-22 matrix self-test 768/768 switches and BUS isolation PASS; "
                         "clip pin map checked SCH/PCB 0 diff (JumperFree_Claude/fable/v04_fw/CHECK.md)"),
        },
    }
    validate_topology(topology)
    return topology
