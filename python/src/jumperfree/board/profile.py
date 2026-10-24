"""Loading boundary for packaged, explicitly unverified board configuration."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping
import tomllib

from ..core import canonical_json
from ..core.shadow import _uint
from ..routing.crossbar import validate_topology
from .types import PlanError, Xp

DEFAULT_BOARD_DIR = Path(__file__).parent / "boards" / "v01"
CHIP_MAP = MappingProxyType({"C1": 0, "C2": 1})  # A4，待实测


@dataclass(frozen=True, slots=True)
class Limits:
    max_ops: int = 256
    brightness_cap: int = 64
    lease_default_ms: int = 0
    t_gap_ms: int = 1

    def __post_init__(self) -> None:
        _uint(self.max_ops, 257, "max_ops")
        _uint(self.brightness_cap, 256, "brightness_cap")
        _uint(self.lease_default_ms, 1 << 32, "lease_default_ms")
        _uint(self.t_gap_ms, 1 << 32, "t_gap_ms")


@dataclass(frozen=True, slots=True)
class VerifiedSection:
    name: str
    verified: bool | str
    evidence: str | None = None


@dataclass(frozen=True, slots=True)
class BoardProfile:
    board_id: str
    rev: str
    schema: str
    topology_json: str
    pinmap_json: str
    calibration_json: str
    limits: Limits
    known_bad_crosspoints: tuple[Xp, ...]
    sections: tuple[VerifiedSection, ...]

    @property
    def topology(self) -> dict[str, Any]:
        return json.loads(self.topology_json)

    @property
    def chip_map(self) -> Mapping[str, int]:
        return CHIP_MAP

    @property
    def topology_id(self) -> str:
        value = {"topology": self.topology, "known_bad_crosspoints": [
            [xp.chip, xp.x, xp.y] for xp in self.known_bad_crosspoints
        ]}
        return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()[:16]

    @property
    def unverified_sections(self) -> tuple[str, ...]:
        return tuple(section.name for section in self.sections if section.verified is False)

    def require_verified(self) -> None:
        if self.unverified_sections:
            raise PlanError("BOARD_UNVERIFIED", "板级配置未经实测", sections=list(self.unverified_sections))

    @classmethod
    def load(cls, path: str | Path | None = None) -> BoardProfile:
        directory = DEFAULT_BOARD_DIR if path is None else Path(path)
        try:
            board = tomllib.loads((directory / "board.toml").read_text(encoding="utf-8"))
            topology = json.loads((directory / "topology.json").read_text(encoding="utf-8"))
            pinmap = tomllib.loads((directory / "pinmap.toml").read_text(encoding="utf-8"))
            limits = tomllib.loads((directory / "limits.toml").read_text(encoding="utf-8"))
            calib = json.loads((directory / "calibration.json").read_text(encoding="utf-8"))
            validate_topology(topology)
            if (board["board_id"] != "v01" or board["schema"] != "jf.board/1"
                    or not isinstance(board["rev"], str) or not board["rev"]
                    or topology["chips"] != [
                        {"name": name, "x_lines": 16, "y_lines": 8} for name in CHIP_MAP
                    ] or calib["schema"] != "jf.calib/1"):
                raise ValueError("unsupported board or schema")
            sections = []
            for name in ("topology", "pinmap", "limits", "calibration"):
                record = board["sections"][name]
                status = record["verified"]
                if status is not False and status not in ("bench", "hil"):
                    raise ValueError("verified must be false, bench or hil")
                evidence = record.get("evidence")
                if status is not False:
                    # Evidence travels with custom board directories; no implicit repo lookup.
                    relative = Path(evidence)
                    if (relative.is_absolute() or ".." in relative.parts
                            or relative.parts[:2] != ("docs", "hw")
                            or not (directory / relative).resolve().is_relative_to(directory.resolve())
                            or not (directory / relative).is_file()):
                        raise ValueError("verified section requires existing docs/hw evidence")
                sections.append(VerifiedSection(name, status, evidence))
            bad = tuple(sorted({Xp(*point) for point in calib["known_bad_crosspoints"]}))
            return cls(board["board_id"], board["rev"], board["schema"], canonical_json(topology),
                       canonical_json(pinmap), canonical_json(calib),
                       Limits(**{key: limits[key] for key in (
                           "max_ops", "brightness_cap", "lease_default_ms", "t_gap_ms"
                       )}), bad, tuple(sections))
        except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
            raise PlanError("BOARD_INVALID", "板级配置无效或不可读", path=str(directory), reason=str(exc)) from exc


def reject_bad_crosspoints(points: Mapping[Xp, object], profile: BoardProfile) -> None:
    """DEFER graph filtering: reject a physical plan touching calibrated bad points."""
    bad = sorted(set(points).intersection(profile.known_bad_crosspoints))
    if bad:
        raise PlanError("PLAN_BAD_CROSSPOINT", "计划使用已知坏交叉点", crosspoints=[
            [point.chip, point.x, point.y] for point in bad
        ])
