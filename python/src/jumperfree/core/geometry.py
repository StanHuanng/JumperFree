"""JF2 wire geometry per firmware board profile (HELLO ``board=`` / ``chips=``).

The geometry only fixes coordinate ranges and line lengths on the wire. Which
clip sits on which crosspoint is board-level data (``board/boards/*``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from . import JfError


@dataclass(frozen=True, slots=True)
class Geometry:
    board: str                 # firmware board id in HELLO/INFO
    chips: int                 # c = 0..chips-1
    x_lines: int               # usable X per chip (v01: X8..15 unwired)
    leds: int                  # LED BATCH clip index range 0..leds-1
    tags_x: int                # X lines covered by SW TAGS (v01 sends only x 0..7)
    max_reply: int             # longest response line incl. LF
    # Service chips: only these y lines accept switch operations.
    restricted: Mapping[int, frozenset[int]] = field(default_factory=lambda: MappingProxyType({}))

    def check_point(self, chip: int, x: int, y: int) -> bool:
        if not (0 <= chip < self.chips and 0 <= x < self.x_lines and 0 <= y < 8):
            return False
        allowed = self.restricted.get(chip)
        return allowed is None or y in allowed


V01 = Geometry("v01", chips=2, x_lines=8, leds=16, tags_x=8, max_reply=512)
# JumperFree mini (PCB v0.4): U301..U306 carry clips, U307 (c=6) is the service
# chip with only y6 TEST_SRC and y7 SVC_GND exposed by the firmware.
MINI = Geometry("mini", chips=7, x_lines=16, leds=48, tags_x=16, max_reply=1024,
               restricted=MappingProxyType({6: frozenset({6, 7})}))

GEOMETRIES: Mapping[str, Geometry] = MappingProxyType({g.board: g for g in (V01, MINI)})


def geometry_for(board: str) -> Geometry:
    try:
        return GEOMETRIES[board]
    except KeyError:
        raise JfError("BOARD_UNSUPPORTED", "no JF2 geometry for this board", board=board) from None
