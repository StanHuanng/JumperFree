"""Validated, immutable routing scenarios and topology adapters.

Scenario files describe logical terminal placement only.  In particular, MCU
clip coordinates are provisional adapter coordinates rather than verified
physical header pinouts.
"""

from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import urlparse

from .crossbar import small_topology
from .model import RouteError

_TOKEN = re.compile(r"^[^.\s]+$")
_CLIP = re.compile(r"^R([0-9]+)C([0-9]+)$")
_MISSING = object()


@dataclass(frozen=True)
class TopologySpec:
    """Effective dimensions used while resolving a scenario."""

    chips: int = 2
    x_lines: int = 4
    y_lines: int = 4
    links_per_pair: int = 1
    clip_rows: int = 2
    clips_per_row: int = 20
    mcu_links: int = 1


@dataclass(frozen=True)
class ClipPosition:
    row: int
    col: int


@dataclass(frozen=True)
class Offset:
    value: int


@dataclass(frozen=True)
class PlacementSpec:
    rows: tuple[int, ...]
    row_range: tuple[int, int] | None


@dataclass(frozen=True)
class McuSpec:
    kind: str
    pins: Mapping[str, ClipPosition]


@dataclass(frozen=True)
class ModuleSpec:
    id: str
    kind: str
    placement: PlacementSpec
    pins: Mapping[str, int | Offset | None]


@dataclass(frozen=True)
class NetSpec:
    id: str
    members: tuple[str, ...]


@dataclass(frozen=True)
class ExpectedSpec:
    routeable: bool
    error_code: str | None = None
    known_limitation: str | None = None


@dataclass(frozen=True)
class Scenario:
    """A fully validated scenario with deterministic, immutable placement."""

    schema_version: int
    id: str
    title: str
    topology: TopologySpec
    mcu: McuSpec
    modules: tuple[ModuleSpec, ...]
    nets: tuple[NetSpec, ...]
    sources: tuple[str, ...]
    assumptions: tuple[str, ...]
    expected: ExpectedSpec
    endpoint_nodes: Mapping[str, str]


class _DuplicateKey(ValueError):
    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


def _pairs_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(key)
        result[key] = value
    return result


def _fail(
    code: str,
    message: str,
    *,
    scenario_id: str | None,
    path: str,
    value: object = _MISSING,
    **details: object,
) -> None:
    payload: dict[str, object] = {"path": path, **details}
    if scenario_id is not None:
        payload["scenario_id"] = scenario_id
    if value is not _MISSING:
        payload["value"] = value
    raise RouteError(code, message, **payload)


def _object(
    value: object,
    *,
    allowed: set[str],
    required: set[str],
    scenario_id: str | None,
    path: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail("INVALID_SCENARIO", "value must be an object", scenario_id=scenario_id,
              path=path, value=value)
    if any(not isinstance(key, str) for key in value):
        _fail("INVALID_SCENARIO", "object keys must be strings", scenario_id=scenario_id,
              path=path)
    unknown = sorted(set(value) - allowed)
    missing = sorted(required - set(value))
    if unknown:
        _fail("INVALID_SCENARIO", "object contains unknown fields",
              scenario_id=scenario_id, path=path, fields=unknown)
    if missing:
        _fail("INVALID_SCENARIO", "object is missing required fields",
              scenario_id=scenario_id, path=path, fields=missing)
    return value


def _text(value: object, scenario_id: str | None, path: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        _fail("INVALID_SCENARIO", "value must be a nonempty trimmed string",
              scenario_id=scenario_id, path=path, value=value)
    return value


def _token(value: object, scenario_id: str | None, path: str) -> str:
    text = _text(value, scenario_id, path)
    if _TOKEN.fullmatch(text) is None:
        _fail("INVALID_SCENARIO", "value must be a dot-free token",
              scenario_id=scenario_id, path=path, value=value)
    return text


def _integer(value: object, scenario_id: str | None, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        _fail("INVALID_SCENARIO", "value must be an integer",
              scenario_id=scenario_id, path=path, value=value)
    return value


def _load_source(source: str | os.PathLike[str] | dict[str, Any]) -> dict[str, Any]:
    if isinstance(source, dict):
        try:
            return copy.deepcopy(source)
        except Exception as exc:
            raise RouteError("INVALID_SCENARIO", "scenario dictionary cannot be copied",
                             path="$", exception=type(exc).__name__) from exc
    if not isinstance(source, (str, os.PathLike)) or isinstance(source, bytes):
        raise RouteError("INVALID_SCENARIO", "scenario source must be a path or dictionary",
                         path="$", value=source)
    try:
        raw = Path(source).read_text(encoding="utf-8")
        parsed = json.loads(
            raw,
            object_pairs_hook=_pairs_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"nonfinite number {value}")
            ),
        )
    except _DuplicateKey as exc:
        raise RouteError("INVALID_SCENARIO", "JSON object contains a duplicate key",
                         path="$", key=exc.key) from exc
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise RouteError("INVALID_SCENARIO", "scenario file is not readable valid JSON",
                         path="$", exception=type(exc).__name__) from exc
    if not isinstance(parsed, dict):
        raise RouteError("INVALID_SCENARIO", "scenario root must be an object", path="$",
                         value=parsed)
    return parsed


def _topology(value: object, scenario_id: str) -> TopologySpec:
    defaults = TopologySpec()
    if value is _MISSING:
        return defaults
    names = {
        "chips", "x_lines", "y_lines", "links_per_pair", "clip_rows",
        "clips_per_row", "mcu_links",
    }
    record = _object(value, allowed=names, required=set(), scenario_id=scenario_id,
                     path="$.topology")
    supplied = {
        name: _integer(record[name], scenario_id, f"$.topology.{name}")
        if name in record else getattr(defaults, name)
        for name in names
    }
    spec = TopologySpec(**supplied)
    if (
        spec.chips <= 0 or spec.x_lines <= 0 or spec.y_lines <= 0
        or spec.links_per_pair < 0 or spec.clip_rows != 2
        or spec.clips_per_row <= 0 or spec.mcu_links <= 0
    ):
        _fail("INVALID_SCENARIO", "scenario topology dimensions are out of range",
              scenario_id=scenario_id, path="$.topology")
    required_y = spec.links_per_pair * (2 if spec.chips > 2 else 1 if spec.chips > 1 else 0)
    bank_start = spec.links_per_pair if spec.chips > 1 else 0
    if required_y > spec.y_lines or bank_start + spec.mcu_links > spec.y_lines:
        _fail("INVALID_SCENARIO", "scenario topology has insufficient Y-line banks",
              scenario_id=scenario_id, path="$.topology")
    return spec


def _clip(value: object, spec: TopologySpec, scenario_id: str, path: str) -> ClipPosition:
    if isinstance(value, str):
        match = _CLIP.fullmatch(value)
        if match is None:
            _fail("INVALID_SCENARIO", "clip must use R<row>C<column> syntax",
                  scenario_id=scenario_id, path=path, value=value)
        row, col = (int(part) for part in match.groups())
    else:
        record = _object(value, allowed={"row", "col"}, required={"row", "col"},
                         scenario_id=scenario_id, path=path)
        row = _integer(record["row"], scenario_id, f"{path}.row")
        col = _integer(record["col"], scenario_id, f"{path}.col")
    if not 0 <= row < spec.clip_rows or not 0 <= col < spec.clips_per_row:
        _fail("MCU_PIN_OUTSIDE_CLIP_ZONE", "MCU pin is outside the clip zone",
              scenario_id=scenario_id, path=path, value=value, row=row, col=col)
    return ClipPosition(row, col)


def _placement(value: object, scenario_id: str, path: str) -> PlacementSpec:
    record = _object(value, allowed={"row_range", "rows"}, required=set(),
                     scenario_id=scenario_id, path=path)
    if set(record) not in ({"row_range"}, {"rows"}):
        _fail("INVALID_SCENARIO", "placement requires exactly one row selector",
              scenario_id=scenario_id, path=path)
    if "row_range" in record:
        raw = record["row_range"]
        if not isinstance(raw, list) or len(raw) != 2:
            _fail("INVALID_SCENARIO", "row_range must contain two integers",
                  scenario_id=scenario_id, path=f"{path}.row_range", value=raw)
        lo = _integer(raw[0], scenario_id, f"{path}.row_range[0]")
        hi = _integer(raw[1], scenario_id, f"{path}.row_range[1]")
        if lo > hi:
            _fail("INVALID_SCENARIO", "row_range lower bound exceeds upper bound",
                  scenario_id=scenario_id, path=f"{path}.row_range", value=raw)
        return PlacementSpec(tuple(range(lo, hi + 1)), (lo, hi))
    raw = record["rows"]
    if not isinstance(raw, list) or not raw:
        _fail("INVALID_SCENARIO", "rows must be a nonempty list",
              scenario_id=scenario_id, path=f"{path}.rows", value=raw)
    rows = tuple(_integer(item, scenario_id, f"{path}.rows[{index}]")
                 for index, item in enumerate(raw))
    if len(set(rows)) != len(rows):
        _fail("INVALID_SCENARIO", "placement rows must be distinct",
              scenario_id=scenario_id, path=f"{path}.rows", value=raw)
    return PlacementSpec(tuple(sorted(rows)), None)


def _pin_position(value: object, scenario_id: str, path: str) -> int | Offset | None:
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    record = _object(value, allowed={"offset"}, required={"offset"},
                     scenario_id=scenario_id, path=path)
    offset = _integer(record["offset"], scenario_id, f"{path}.offset")
    if offset < 0:
        _fail("INVALID_SCENARIO", "pin offset must be nonnegative",
              scenario_id=scenario_id, path=f"{path}.offset", value=offset)
    return Offset(offset)


def _pin_map(value: object, scenario_id: str, path: str) -> dict[str, object]:
    if not isinstance(value, dict) or not value:
        _fail("INVALID_SCENARIO", "pins must be a nonempty object",
              scenario_id=scenario_id, path=path, value=value)
    result: dict[str, object] = {}
    for name, position in value.items():
        pin = _token(name, scenario_id, f"{path}.<pin>")
        result[pin] = position
    return result


def _parse_mcu(value: object, spec: TopologySpec, scenario_id: str) -> McuSpec:
    record = _object(value, allowed={"kind", "pins"}, required={"kind", "pins"},
                     scenario_id=scenario_id, path="$.mcu")
    pins = {
        name: _clip(position, spec, scenario_id, f"$.mcu.pins.{name}")
        for name, position in _pin_map(record["pins"], scenario_id, "$.mcu.pins").items()
    }
    occupied: dict[ClipPosition, str] = {}
    for name in sorted(pins):
        if pins[name] in occupied:
            _fail("CONFLICTING_PIN_PLACEMENT", "MCU pins share one clip",
                  scenario_id=scenario_id, path=f"$.mcu.pins.{name}", pin=name,
                  other_pin=occupied[pins[name]])
        occupied[pins[name]] = name
    return McuSpec(_text(record["kind"], scenario_id, "$.mcu.kind"),
                   MappingProxyType(dict(sorted(pins.items()))))


def _parse_modules(value: object, scenario_id: str) -> tuple[ModuleSpec, ...]:
    if not isinstance(value, list) or not value:
        _fail("INVALID_SCENARIO", "modules must be a nonempty list",
              scenario_id=scenario_id, path="$.modules", value=value)
    modules: list[ModuleSpec] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        path = f"$.modules[{index}]"
        record = _object(item, allowed={"id", "kind", "placement", "pins"},
                         required={"id", "kind", "placement", "pins"},
                         scenario_id=scenario_id, path=path)
        module_id = _token(record["id"], scenario_id, f"{path}.id")
        if module_id == "mcu" or module_id in seen:
            _fail("INVALID_SCENARIO", "module IDs must be unique and cannot be mcu",
                  scenario_id=scenario_id, path=f"{path}.id", value=module_id)
        seen.add(module_id)
        raw_pins = _pin_map(record["pins"], scenario_id, f"{path}.pins")
        pins = {
            name: _pin_position(position, scenario_id, f"{path}.pins.{name}")
            for name, position in raw_pins.items()
        }
        modules.append(ModuleSpec(
            module_id,
            _text(record["kind"], scenario_id, f"{path}.kind"),
            _placement(record["placement"], scenario_id, f"{path}.placement"),
            MappingProxyType(dict(sorted(pins.items()))),
        ))
    return tuple(sorted(modules, key=lambda module: module.id))


def _parse_nets(value: object, scenario_id: str) -> tuple[NetSpec, ...]:
    if not isinstance(value, list) or not value:
        _fail("INVALID_SCENARIO", "nets must be a nonempty list",
              scenario_id=scenario_id, path="$.nets", value=value)
    nets: list[NetSpec] = []
    seen_ids: set[str] = set()
    endpoint_net: dict[str, str] = {}
    for index, item in enumerate(value):
        path = f"$.nets[{index}]"
        record = _object(item, allowed={"id", "members"}, required={"id", "members"},
                         scenario_id=scenario_id, path=path)
        net_id = _token(record["id"], scenario_id, f"{path}.id")
        if net_id in seen_ids:
            _fail("INVALID_SCENARIO", "net IDs must be unique", scenario_id=scenario_id,
                  path=f"{path}.id", value=net_id)
        seen_ids.add(net_id)
        raw_members = record["members"]
        if not isinstance(raw_members, list) or len(raw_members) < 2:
            _fail("INVALID_SCENARIO", "a net needs at least two members",
                  scenario_id=scenario_id, path=f"{path}.members", value=raw_members)
        members: list[str] = []
        for member_index, item_member in enumerate(raw_members):
            member_path = f"{path}.members[{member_index}]"
            member = _text(item_member, scenario_id, member_path)
            parts = member.split(".")
            if len(parts) != 2 or any(_TOKEN.fullmatch(part) is None for part in parts):
                _fail("INVALID_SCENARIO", "endpoint must be module.pin",
                      scenario_id=scenario_id, path=member_path, value=member)
            if member in members:
                _fail("INVALID_SCENARIO", "net members must be distinct",
                      scenario_id=scenario_id, path=member_path, endpoint=member)
            if member in endpoint_net:
                _fail("INVALID_SCENARIO", "endpoint belongs to more than one net",
                      scenario_id=scenario_id, path=member_path, endpoint=member,
                      other_net=endpoint_net[member])
            members.append(member)
            endpoint_net[member] = net_id
        nets.append(NetSpec(net_id, tuple(sorted(members))))
    return tuple(sorted(nets, key=lambda net: net.id))


def _parse_sources(value: object, scenario_id: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        _fail("INVALID_SCENARIO", "sources must be a nonempty URL list",
              scenario_id=scenario_id, path="$.sources", value=value)
    sources: list[str] = []
    for index, item in enumerate(value):
        path = f"$.sources[{index}]"
        source = _text(item, scenario_id, path)
        try:
            parsed = urlparse(source)
            valid = parsed.scheme in {"http", "https"} and bool(parsed.netloc)
        except ValueError:
            valid = False
        if not valid:
            _fail("INVALID_SCENARIO", "source must be an HTTP(S) URL",
                  scenario_id=scenario_id, path=path, value=item)
        sources.append(source)
    return tuple(sources)


def _parse_assumptions(value: object, scenario_id: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        _fail("INVALID_SCENARIO", "assumptions must be a list",
              scenario_id=scenario_id, path="$.assumptions", value=value)
    return tuple(_text(item, scenario_id, f"$.assumptions[{index}]")
                 for index, item in enumerate(value))


def _parse_expected(value: object, scenario_id: str) -> ExpectedSpec:
    allowed = {"routeable", "error_code", "known_limitation"}
    record = _object(value, allowed=allowed, required={"routeable"},
                     scenario_id=scenario_id, path="$.expected")
    routeable = record["routeable"]
    if not isinstance(routeable, bool):
        _fail("INVALID_SCENARIO", "expected.routeable must be boolean",
              scenario_id=scenario_id, path="$.expected.routeable", value=routeable)
    extras = set(record) - {"routeable"}
    if routeable and extras:
        _fail("INVALID_SCENARIO", "positive expectations forbid failure fields",
              scenario_id=scenario_id, path="$.expected", fields=sorted(extras))
    if not routeable and extras != {"error_code", "known_limitation"}:
        _fail("INVALID_SCENARIO", "negative expectations require failure fields",
              scenario_id=scenario_id, path="$.expected")
    if routeable:
        return ExpectedSpec(True)
    return ExpectedSpec(
        False,
        _token(record["error_code"], scenario_id, "$.expected.error_code"),
        _text(record["known_limitation"], scenario_id, "$.expected.known_limitation"),
    )


def _check_references(
    mcu: McuSpec, modules: tuple[ModuleSpec, ...], nets: tuple[NetSpec, ...], scenario_id: str
) -> None:
    known: dict[str, set[str]] = {"mcu": set(mcu.pins)}
    known.update({module.id: set(module.pins) for module in modules})
    for net in nets:
        for member in net.members:
            owner, pin = member.split(".")
            if owner not in known or pin not in known[owner]:
                _fail("UNKNOWN_PIN", "net references an unknown module or pin",
                      scenario_id=scenario_id, path=f"$.nets.{net.id}", endpoint=member,
                      module=owner, pin=pin)


def _validate_placement_sets(
    modules: tuple[ModuleSpec, ...], spec: TopologySpec, scenario_id: str
) -> None:
    row_count = spec.chips * spec.x_lines
    for module in modules:
        invalid = [row for row in module.placement.rows if not 0 <= row < row_count]
        if invalid:
            _fail("PLACEMENT_OUT_OF_RANGE", "module placement exceeds the breadboard",
                  scenario_id=scenario_id, path=f"$.modules.{module.id}.placement",
                  module=module.id, value=invalid)
        fixed = [position for position in module.pins.values() if isinstance(position, int)]
        if len(module.pins) > len(module.placement.rows):
            _fail("PLACEMENT_OUT_OF_RANGE", "module pins cannot fit in its placement",
                  scenario_id=scenario_id, path=f"$.modules.{module.id}.placement",
                  module=module.id)
        for pin, position in module.pins.items():
            if isinstance(position, int) and position not in module.placement.rows:
                _fail("PLACEMENT_OUT_OF_RANGE", "fixed pin lies outside module placement",
                      scenario_id=scenario_id, path=f"$.modules.{module.id}.pins.{pin}",
                      module=module.id, pin=pin, value=position)
        if len(set(fixed)) != len(fixed):
            _fail("CONFLICTING_PIN_PLACEMENT", "module fixed pins share one row",
                  scenario_id=scenario_id, path=f"$.modules.{module.id}.pins",
                  module=module.id)


def _resolve(
    mcu: McuSpec, modules: tuple[ModuleSpec, ...], spec: TopologySpec, scenario_id: str
) -> Mapping[str, str]:
    _validate_placement_sets(modules, spec, scenario_id)
    resolved = {
        f"mcu.{pin}": f"MCU_R{position.row}_C{position.col}"
        for pin, position in mcu.pins.items()
    }
    occupied: dict[int, str] = {}
    for module in modules:
        for pin in sorted(module.pins):
            position = module.pins[pin]
            if not isinstance(position, int):
                continue
            endpoint = f"{module.id}.{pin}"
            if position in occupied:
                _fail("CONFLICTING_PIN_PLACEMENT", "fixed module pins share one row",
                      scenario_id=scenario_id, path=f"$.modules.{module.id}.pins.{pin}",
                      module=module.id, pin=pin, row=position,
                      other_endpoint=occupied[position])
            occupied[position] = endpoint
            resolved[endpoint] = f"N{position}"

    for module in modules:
        relative = {
            pin: position.value for pin, position in module.pins.items()
            if isinstance(position, Offset)
        }
        if len(set(relative.values())) != len(relative):
            _fail("CONFLICTING_PIN_PLACEMENT", "relative pins share one offset",
                  scenario_id=scenario_id, path=f"$.modules.{module.id}.pins",
                  module=module.id)
        targets: dict[str, int] = {}
        if relative:
            structural = [
                anchor for anchor in module.placement.rows
                if all(anchor + offset in module.placement.rows for offset in relative.values())
            ]
            if not structural:
                _fail("PLACEMENT_OUT_OF_RANGE", "relative pin layout cannot fit placement",
                      scenario_id=scenario_id, path=f"$.modules.{module.id}.placement",
                      module=module.id)
            chosen: int | None = None
            for anchor in structural:
                rows = {anchor + offset for offset in relative.values()}
                if not rows.intersection(occupied):
                    chosen = anchor
                    break
            if chosen is None:
                _fail("CONFLICTING_PIN_PLACEMENT", "relative layout has no free realization",
                      scenario_id=scenario_id, path=f"$.modules.{module.id}.placement",
                      module=module.id)
            targets = {pin: chosen + offset for pin, offset in relative.items()}
        for pin in sorted(targets):
            row = targets[pin]
            endpoint = f"{module.id}.{pin}"
            occupied[row] = endpoint
            resolved[endpoint] = f"N{row}"

    for module in modules:
        nulls = sorted(pin for pin, position in module.pins.items() if position is None)
        free_rows = [row for row in module.placement.rows if row not in occupied]
        if len(free_rows) < len(nulls):
            _fail("CONFLICTING_PIN_PLACEMENT", "automatic pins have too few free rows",
                  scenario_id=scenario_id, path=f"$.modules.{module.id}.placement",
                  module=module.id)
        for pin, row in zip(nulls, free_rows, strict=False):
            endpoint = f"{module.id}.{pin}"
            occupied[row] = endpoint
            resolved[endpoint] = f"N{row}"
    return MappingProxyType(dict(sorted(resolved.items())))


def load_scenario(source: str | os.PathLike[str] | dict[str, Any]) -> Scenario:
    """Load, validate, normalize, and deterministically place a scenario."""

    data = _load_source(source)
    tentative_id = data.get("id") if isinstance(data.get("id"), str) else None
    required = {"id", "title", "mcu", "modules", "nets", "sources", "assumptions", "expected"}
    allowed = required | {"schema_version", "topology"}
    root = _object(data, allowed=allowed, required=required, scenario_id=tentative_id,
                   path="$")
    scenario_id = _token(root["id"], tentative_id, "$.id")
    version = root.get("schema_version", 1)
    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
        _fail("INVALID_SCENARIO", "only scenario schema version 1 is supported",
              scenario_id=scenario_id, path="$.schema_version", value=version)
    spec = _topology(root.get("topology", _MISSING), scenario_id)
    mcu = _parse_mcu(root["mcu"], spec, scenario_id)
    modules = _parse_modules(root["modules"], scenario_id)
    nets = _parse_nets(root["nets"], scenario_id)
    title = _text(root["title"], scenario_id, "$.title")
    sources = _parse_sources(root["sources"], scenario_id)
    assumptions = _parse_assumptions(root["assumptions"], scenario_id)
    expected = _parse_expected(root["expected"], scenario_id)
    _check_references(mcu, modules, nets, scenario_id)
    endpoint_nodes = _resolve(mcu, modules, spec, scenario_id)
    return Scenario(
        version,
        scenario_id,
        title,
        spec,
        mcu,
        modules,
        nets,
        sources,
        assumptions,
        expected,
        endpoint_nodes,
    )


def scenario_to_topology(
    scenario: Scenario,
    *,
    chips: int | None = None,
    x_lines: int | None = None,
    y_lines: int | None = None,
    links_per_pair: int | None = None,
    mcu_links: int | None = None,
) -> dict[str, Any]:
    """Build the MCU-zone topology for a loaded scenario."""

    if not isinstance(scenario, Scenario):
        raise RouteError("INVALID_SCENARIO", "scenario must be a loaded Scenario", path="$")
    values = {
        "chips": scenario.topology.chips if chips is None else chips,
        "x_lines": scenario.topology.x_lines if x_lines is None else x_lines,
        "y_lines": scenario.topology.y_lines if y_lines is None else y_lines,
        "links_per_pair": (
            scenario.topology.links_per_pair if links_per_pair is None else links_per_pair
        ),
        "mcu_links": scenario.topology.mcu_links if mcu_links is None else mcu_links,
    }
    topology = small_topology(
        values["chips"], values["x_lines"], values["y_lines"],
        links_per_pair=values["links_per_pair"], node_prefix="N", mcu_zone=True,
        clip_rows=scenario.topology.clip_rows,
        clips_per_row=scenario.topology.clips_per_row,
        mcu_links=values["mcu_links"],
    )
    row_count = values["chips"] * values["x_lines"]
    used_rows = [
        int(node[1:]) for endpoint, node in scenario.endpoint_nodes.items()
        if not endpoint.startswith("mcu.")
    ]
    if used_rows and max(used_rows) >= row_count:
        _fail("PLACEMENT_OUT_OF_RANGE", "topology override removes a resolved row",
              scenario_id=scenario.id, path="$.topology", value=max(used_rows))
    return topology


def scenario_to_route_requests(scenario: Scenario) -> list[tuple[str, list[str]]]:
    """Return stable net requests independent of topology-adapter call order."""

    if not isinstance(scenario, Scenario):
        raise RouteError("INVALID_SCENARIO", "scenario must be a loaded Scenario", path="$")
    return [
        (net.id, sorted(scenario.endpoint_nodes[member] for member in net.members))
        for net in scenario.nets
    ]


__all__ = [
    "ClipPosition", "ExpectedSpec", "McuSpec", "ModuleSpec", "NetSpec", "Offset",
    "PlacementSpec", "Scenario", "TopologySpec", "load_scenario",
    "scenario_to_route_requests", "scenario_to_topology",
]
