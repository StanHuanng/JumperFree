"""Loading boundary for packaged board configuration (v01 and JumperFree mini)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping
import tomllib

from ..core import JfError, canonical_json
from ..core.geometry import Geometry, geometry_for
from ..core.shadow import _uint
from ..routing.crossbar import validate_topology
from .types import PlanError, Xp

BOARDS_DIR = Path(__file__).parent / "boards"
DEFAULT_BOARD_DIR = BOARDS_DIR / "v01"
CHIP_MAP = MappingProxyType({"C1": 0, "C2": 1})  # v01 A4，待实测
_BOARD_IDS = {"v01": "v01", "mini": "v04"}  # profile id -> firmware board id in HELLO


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
    wire_board: str = "v01"
    name: str = "JumperFree v0.1"
    nodes_json: str = "{}"

    @property
    def topology(self) -> dict[str, Any]:
        return json.loads(self.topology_json)

    @property
    def geometry(self) -> Geometry:
        return geometry_for(self.wire_board)

    @property
    def chip_map(self) -> Mapping[str, int]:
        if self.wire_board == "v01":
            return CHIP_MAP
        return MappingProxyType({chip["name"]: index for index, chip in enumerate(self.topology["chips"])})

    @property
    def nodes(self) -> dict[str, dict[str, Any]]:
        """Node table. v01: Clip0..Clip15, LED index = clip number."""
        table = json.loads(self.nodes_json)
        if not table:
            table = {f"Clip{n}": {"kind": "clip", "led": n} for n in range(16)}
        return table

    @property
    def public_nodes(self) -> tuple[str, ...]:
        """Names an Agent may use: clips and service nodes, never reserved ones."""
        return tuple(name for name, node in self.nodes.items() if node["kind"] in {"clip", "service"})

    @property
    def led_map(self) -> dict[str, int]:
        return {name: node["led"] for name, node in self.nodes.items() if "led" in node}

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
    def for_board(cls, board: str) -> BoardProfile:
        """Load the packaged profile by profile id (v01, mini) or firmware board id (v04)."""
        for profile_id, wire in _BOARD_IDS.items():
            if board in (profile_id, wire):
                return cls.load(BOARDS_DIR / profile_id)
        raise PlanError("BOARD_UNSUPPORTED", "no packaged board profile", board=board)

    @classmethod
    def load(cls, path: str | Path | None = None) -> BoardProfile:
        directory = DEFAULT_BOARD_DIR if path is None else Path(path)
        try:
            board = tomllib.loads((directory / "board.toml").read_text(encoding="utf-8"))
            topology = json.loads((directory / "topology.json").read_text(encoding="utf-8"))
            pinmap = tomllib.loads((directory / "pinmap.toml").read_text(encoding="utf-8"))
            limits = tomllib.loads((directory / "limits.toml").read_text(encoding="utf-8"))
            calib = json.loads((directory / "calibration.json").read_text(encoding="utf-8"))
            nodes_path = directory / "nodes.json"
            nodes = json.loads(nodes_path.read_text(encoding="utf-8")) if nodes_path.exists() else {}
            validate_topology(topology)
            board_id = board["board_id"]
            wire = board.get("wire_board", "v01")
            if (board_id not in _BOARD_IDS or _BOARD_IDS[board_id] != wire
                    or board["schema"] != "jf.board/1"
                    or not isinstance(board["rev"], str) or not board["rev"]
                    or calib["schema"] != "jf.calib/1"):
                raise ValueError("unsupported board or schema")
            geometry = geometry_for(wire)
            if wire == "v01":
                expected = [{"name": name, "x_lines": 16, "y_lines": 8} for name in CHIP_MAP]
                if topology["chips"] != expected or nodes:
                    raise ValueError("v01 topology must use chips C1, C2 and no node table")
            else:
                if (len(topology["chips"]) != geometry.chips
                        or any(chip["x_lines"] != 16 or chip["y_lines"] != 8 for chip in topology["chips"])):
                    raise ValueError("topology chips differ from the firmware geometry")
                if set(nodes) != set(topology["node_attachments"]):
                    raise ValueError("node table must cover exactly the topology nodes")
                for name, node in nodes.items():
                    if node.get("kind") not in {"clip", "service", "reserved"}:
                        raise ValueError(f"node {name}: unknown kind")
                    led = node.get("led")
                    if node["kind"] == "clip" and (type(led) is not int or not 0 <= led < geometry.leds):
                        raise ValueError(f"node {name}: clip needs an LED index")
                leds = [node["led"] for node in nodes.values() if "led" in node]
                if len(leds) != len(set(leds)):
                    raise ValueError("LED indexes must be unique")
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
            return cls(board_id, board["rev"], board["schema"], canonical_json(topology),
                       canonical_json(pinmap), canonical_json(calib),
                       Limits(**{key: limits[key] for key in (
                           "max_ops", "brightness_cap", "lease_default_ms", "t_gap_ms"
                       )}), bad, tuple(sections), wire, board.get("name", "JumperFree v0.1"),
                       canonical_json(nodes))
        except (OSError, UnicodeError, ValueError, KeyError, TypeError, JfError) as exc:
            raise PlanError("BOARD_INVALID", "板级配置无效或不可读", path=str(directory), reason=str(exc)) from exc


def reject_bad_crosspoints(points: Mapping[Xp, object], profile: BoardProfile) -> None:
    """DEFER graph filtering: reject a physical plan touching calibrated bad points."""
    bad = sorted(set(points).intersection(profile.known_bad_crosspoints))
    if bad:
        raise PlanError("PLAN_BAD_CROSSPOINT", "计划使用已知坏交叉点", crosspoints=[
            [point.chip, point.x, point.y] for point in bad
        ])
