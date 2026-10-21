"""Generic resource graph for crossbar-based routing topologies.

This module deliberately models logical resources only.  It contains no
device-register, address, or transport details for a particular crossbar.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .model import RouteError

LineVertex = tuple[str, str, int]
Vertex = str | LineVertex
Edge = tuple[Vertex, Vertex]


@dataclass(frozen=True)
class ResourceGraph:
    """A deterministic undirected graph of fixed and switchable resources.

    String vertices are external node identifiers.  Three-item tuple vertices
    are chip line endpoints in ``(chip, axis, line)`` form.  Every entry in
    ``exclusive_lines`` may be owned by at most one electrical net.
    """

    vertices: tuple[Vertex, ...]
    fixed_edges: tuple[Edge, ...]
    crosspoint_edges: tuple[Edge, ...]
    exclusive_lines: tuple[LineVertex, ...]


def _invalid(message: str, **details: object) -> None:
    raise RouteError("INVALID_TOPOLOGY", message, **details)


def _vertex_key(vertex: Vertex) -> tuple[str, str, str, int]:
    if isinstance(vertex, str):
        return "node", vertex, "", -1
    return "line", vertex[0], vertex[1], vertex[2]


def _edge(left: Vertex, right: Vertex) -> Edge:
    return (left, right) if _vertex_key(left) <= _vertex_key(right) else (right, left)


def _endpoint(
    value: object, chips: Mapping[str, tuple[int, int]], location: str
) -> LineVertex:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        _invalid("line endpoint must be [chip, axis, line]", location=location)
    chip, axis, line = value
    if not isinstance(chip, str) or chip not in chips:
        _invalid("line endpoint references an unknown chip", location=location, chip=chip)
    if axis not in ("X", "Y"):
        _invalid("line endpoint axis must be X or Y", location=location, axis=axis)
    if not isinstance(line, int) or isinstance(line, bool):
        _invalid("line number must be an integer", location=location, line=line)
    limit = chips[chip][0 if axis == "X" else 1]
    if not 0 <= line < limit:
        _invalid("line number is out of range", location=location, line=line, limit=limit)
    return chip, axis, line


def _validated_parts(
    topology: object,
) -> tuple[dict[str, tuple[int, int]], dict[str, LineVertex], list[Edge]]:
    if not isinstance(topology, dict):
        _invalid("topology must be a dictionary")
    required = {"chips", "node_attachments", "inter_chip_links"}
    if not required.issubset(topology):
        _invalid("topology is missing required fields", missing=sorted(required - topology.keys()))
    chip_records = topology["chips"]
    attachments_record = topology["node_attachments"]
    links_record = topology["inter_chip_links"]
    if not isinstance(chip_records, list) or not chip_records:
        _invalid("chips must be a non-empty list")
    if not isinstance(attachments_record, dict):
        _invalid("node_attachments must be a dictionary")
    if not isinstance(links_record, list):
        _invalid("inter_chip_links must be a list")

    chips: dict[str, tuple[int, int]] = {}
    for index, record in enumerate(chip_records):
        if not isinstance(record, dict):
            _invalid("chip entry must be a dictionary", index=index)
        name = record.get("name")
        x_lines = record.get("x_lines")
        y_lines = record.get("y_lines")
        if not isinstance(name, str) or not name:
            _invalid("chip name must be a non-empty string", index=index)
        if name in chips:
            _invalid("chip names must be unique", chip=name)
        if (
            not isinstance(x_lines, int)
            or isinstance(x_lines, bool)
            or x_lines <= 0
            or not isinstance(y_lines, int)
            or isinstance(y_lines, bool)
            or y_lines <= 0
        ):
            _invalid("chip line counts must be positive integers", chip=name)
        chips[name] = (x_lines, y_lines)

    attachments: dict[str, LineVertex] = {}
    for node, value in attachments_record.items():
        if not isinstance(node, str) or not node:
            _invalid("node id must be a non-empty string", node=node)
        endpoint = _endpoint(value, chips, f"node_attachments.{node}")
        attachments[node] = endpoint

    links: list[Edge] = []
    seen_links: set[Edge] = set()
    for index, value in enumerate(links_record):
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            _invalid("inter-chip link must contain two endpoints", index=index)
        left = _endpoint(value[0], chips, f"inter_chip_links.{index}.0")
        right = _endpoint(value[1], chips, f"inter_chip_links.{index}.1")
        if left[0] == right[0]:
            _invalid("inter-chip link endpoints must be on different chips", index=index)
        edge = _edge(left, right)
        if edge in seen_links:
            _invalid("inter-chip links may not be duplicated", index=index)
        links.append(edge)
        seen_links.add(edge)
    return chips, attachments, links


def validate_topology(topology: object) -> None:
    """Validate topology shape and references, raising ``INVALID_TOPOLOGY``."""

    _validated_parts(topology)


def build_resource_graph(topology: object) -> ResourceGraph:
    """Build a deterministic undirected resource graph from ``topology``."""

    chips, attachments, links = _validated_parts(topology)
    lines = {
        (chip, axis, line)
        for chip, (x_count, y_count) in chips.items()
        for axis, count in (("X", x_count), ("Y", y_count))
        for line in range(count)
    }
    crosspoints = {
        _edge((chip, "X", x_line), (chip, "Y", y_line))
        for chip, (x_count, y_count) in chips.items()
        for x_line in range(x_count)
        for y_line in range(y_count)
    }
    attachment_edges = {_edge(node, endpoint) for node, endpoint in attachments.items()}
    vertices: set[Vertex] = set(lines)
    vertices.update(attachments)
    edge_key = lambda item: (_vertex_key(item[0]), _vertex_key(item[1]))
    return ResourceGraph(
        vertices=tuple(sorted(vertices, key=_vertex_key)),
        fixed_edges=tuple(sorted(attachment_edges | set(links), key=edge_key)),
        crosspoint_edges=tuple(sorted(crosspoints, key=edge_key)),
        exclusive_lines=tuple(sorted(lines, key=_vertex_key)),
    )


def small_topology(
    chips: int = 2,
    x_lines: int = 4,
    y_lines: int = 4,
    *,
    links_per_pair: int = 1,
    node_prefix: str = "N",
    mcu_zone: bool = False,
    clip_rows: int = 2,
    clips_per_row: int = 20,
    mcu_links: int = 1,
) -> dict[str, Any]:
    """Return a deterministic, JSON-compatible topology useful in tests.

    Every X line has one external node.  Adjacent chips are joined in a chain
    using distinct Y lines; therefore an internal chip needs two Y-line banks.
    """

    values = (chips, x_lines, y_lines, links_per_pair)
    if any(not isinstance(value, int) or isinstance(value, bool) for value in values):
        _invalid("small topology dimensions must be integers")
    if chips <= 0 or x_lines <= 0 or y_lines <= 0 or links_per_pair < 0:
        _invalid("small topology dimensions are out of range")
    required_y = links_per_pair * (2 if chips > 2 else 1 if chips > 1 else 0)
    if required_y > y_lines:
        _invalid("not enough Y lines for the requested inter-chip links")
    if not isinstance(node_prefix, str) or not node_prefix:
        _invalid("node_prefix must be a non-empty string")
    if not isinstance(mcu_zone, bool):
        _invalid("mcu_zone must be a boolean")

    chip_names = [f"C{index}" for index in range(chips)]
    topology: dict[str, Any] = {
        "chips": [
            {"name": name, "x_lines": x_lines, "y_lines": y_lines}
            for name in chip_names
        ],
        "node_attachments": {
            f"{node_prefix}{chip_index * x_lines + line}": [name, "X", line]
            for chip_index, name in enumerate(chip_names)
            for line in range(x_lines)
        },
        "inter_chip_links": [
            [[chip_names[index], "Y", links_per_pair + line if index else line],
             [chip_names[index + 1], "Y", line]]
            for index in range(chips - 1)
            for line in range(links_per_pair)
        ],
    }
    if mcu_zone:
        zone_values = (clip_rows, clips_per_row, mcu_links)
        if any(
            not isinstance(value, int) or isinstance(value, bool)
            for value in zone_values
        ):
            _invalid("MCU zone dimensions must be integers")
        if clip_rows != 2 or clips_per_row <= 0 or mcu_links <= 0:
            _invalid("MCU zone dimensions are out of range")
        bank_start = links_per_pair if chips > 1 else 0
        if bank_start + mcu_links > y_lines:
            _invalid("not enough Y lines for the requested MCU links")

        clip_attachments = {
            f"MCU_R{row}_C{column}": ["MCU0", "X", row * clips_per_row + column]
            for row in range(clip_rows)
            for column in range(clips_per_row)
        }
        collisions = sorted(set(topology["node_attachments"]).intersection(clip_attachments))
        if collisions:
            _invalid("MCU clip node names collide with breadboard nodes", nodes=collisions)
        topology["chips"].append({
            "name": "MCU0",
            "x_lines": clip_rows * clips_per_row,
            "y_lines": mcu_links,
        })
        topology["node_attachments"].update(clip_attachments)
        topology["inter_chip_links"].extend(
            [["MCU0", "Y", line], ["C0", "Y", bank_start + line]]
            for line in range(mcu_links)
        )
        topology["mcu_zone"] = {
            "clip_rows": clip_rows,
            "clips_per_row": clips_per_row,
            "chip": "MCU0",
            "axis": "X",
        }
        topology["metadata"] = {
            "hardware_assumptions": [{
                "id": "dedicated_mcu_clip_zone",
                "verified": False,
                "description": "Two clip rows outside breadboard; independent routable clips",
            }],
            "model": "logical_simulation",
        }
    validate_topology(topology)
    return topology


def _closed_edge(value: object) -> Edge:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 3
        or not isinstance(value[0], str)
        or not isinstance(value[1], int)
        or isinstance(value[1], bool)
        or not isinstance(value[2], int)
        or isinstance(value[2], bool)
    ):
        raise RouteError(
            "INVALID_CROSSPOINT", "closed crosspoint must be [chip, x_line, y_line]"
        )
    return _edge((value[0], "X", value[1]), (value[0], "Y", value[2]))


def _node_assignments(
    assignments: Mapping[str, str | Iterable[str]],
) -> dict[str, str]:
    if all(isinstance(value, str) for value in assignments.values()):
        return {node: str(net) for node, net in assignments.items()}
    result: dict[str, str] = {}
    for net, nodes in assignments.items():
        if isinstance(nodes, str):
            raise RouteError("INVALID_NET_ASSIGNMENT", "net-to-nodes values must be iterable")
        for node in nodes:
            if node in result and result[node] != net:
                raise RouteError("INVALID_NET_ASSIGNMENT", "a node belongs to multiple nets")
            result[node] = net
    return result


def find_shorts(
    topology_or_graph: object,
    closed_crosspoints: Iterable[Sequence[object]],
    net_assignments: Mapping[str, str | Iterable[str]],
) -> list[dict[str, object]]:
    """Report connected components containing nodes assigned to different nets.

    ``closed_crosspoints`` uses ``(chip, x_line, y_line)`` records.  Assignments
    may be either ``node -> net`` or ``net -> iterable[node]`` mappings.
    """

    graph = (
        topology_or_graph
        if isinstance(topology_or_graph, ResourceGraph)
        else build_resource_graph(topology_or_graph)
    )
    allowed = set(graph.crosspoint_edges)
    closed = {_closed_edge(value) for value in closed_crosspoints}
    unknown = closed - allowed
    if unknown:
        raise RouteError(
            "INVALID_CROSSPOINT", "closed crosspoint is not in the graph", edge=min(unknown, key=str)
        )
    assignments = _node_assignments(net_assignments)
    unknown_nodes = sorted(set(assignments) - {item for item in graph.vertices if isinstance(item, str)})
    if unknown_nodes:
        raise RouteError("INVALID_NET_ASSIGNMENT", "assignment references unknown nodes", nodes=unknown_nodes)

    parent = {vertex: vertex for vertex in graph.vertices}

    def find(vertex: Vertex) -> Vertex:
        while parent[vertex] != vertex:
            parent[vertex] = parent[parent[vertex]]
            vertex = parent[vertex]
        return vertex

    for left, right in (*graph.fixed_edges, *sorted(closed, key=str)):
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    components: dict[Vertex, list[str]] = {}
    for node in sorted(assignments):
        components.setdefault(find(node), []).append(node)
    shorts = [
        {"nets": sorted({assignments[node] for node in nodes}), "nodes": nodes}
        for nodes in components.values()
        if len({assignments[node] for node in nodes}) > 1
    ]
    return sorted(shorts, key=lambda item: (item["nets"], item["nodes"]))
