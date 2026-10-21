"""Deterministic multi-hop routing on a crossbar resource graph."""

from __future__ import annotations

import copy
import heapq
from collections import deque
from typing import Any, Iterable, Mapping

from .crossbar import LineVertex, ResourceGraph, Vertex, build_resource_graph, find_shorts
from .model import RouteError

CROSSBAR_STATE_VERSION = 1
CROSSBAR_STATE_MODEL = "crossbar"


def crossbar_init_state(topology: object) -> dict[str, Any]:
    """Return an empty, JSON-serialisable state for ``topology``."""

    build_resource_graph(topology)
    return {
        "model": CROSSBAR_STATE_MODEL,
        "nets": {},
        "topology": copy.deepcopy(topology),
        "version": CROSSBAR_STATE_VERSION,
    }


def _invalid_state() -> None:
    raise RouteError("INVALID_STATE", "crossbar routing state has an invalid shape or version")


def _line(value: object) -> LineVertex:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 3
        or not isinstance(value[0], str)
        or value[1] not in ("X", "Y")
        or not isinstance(value[2], int)
        or isinstance(value[2], bool)
    ):
        _invalid_state()
    return value[0], value[1], value[2]


def _crosspoint(value: object) -> tuple[str, int, int]:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 3
        or not isinstance(value[0], str)
        or not isinstance(value[1], int)
        or isinstance(value[1], bool)
        or not isinstance(value[2], int)
        or isinstance(value[2], bool)
    ):
        _invalid_state()
    return value[0], value[1], value[2]


def _validated_state(state: object) -> tuple[dict[str, Any], ResourceGraph]:
    if (
        not isinstance(state, dict)
        or state.get("version") != CROSSBAR_STATE_VERSION
        or state.get("model") != CROSSBAR_STATE_MODEL
        or not isinstance(state.get("nets"), dict)
        or "topology" not in state
    ):
        _invalid_state()
    try:
        graph = build_resource_graph(state["topology"])
    except RouteError as exc:
        raise RouteError("INVALID_STATE", "crossbar state contains an invalid topology") from exc

    known_nodes = {vertex for vertex in graph.vertices if isinstance(vertex, str)}
    known_lines = set(graph.exclusive_lines)
    known_crosspoints = {_point(left, right) for left, right in graph.crosspoint_edges}
    seen_nodes: set[str] = set()
    seen_lines: set[LineVertex] = set()
    for net_id, net in state["nets"].items():
        if (
            not isinstance(net_id, str)
            or not isinstance(net, dict)
            or not isinstance(net.get("nodes"), list)
            or not isinstance(net.get("lines"), list)
            or not isinstance(net.get("crosspoints"), list)
        ):
            _invalid_state()
        nodes = net["nodes"]
        lines = [_line(value) for value in net["lines"]]
        crosspoints = [_crosspoint(value) for value in net["crosspoints"]]
        if (
            len(nodes) < 2
            or any(not isinstance(node, str) for node in nodes)
            or nodes != sorted(set(nodes))
            or set(nodes) - known_nodes
            or lines != sorted(set(lines))
            or set(lines) - known_lines
            or crosspoints != sorted(set(crosspoints))
            or set(crosspoints) - known_crosspoints
            or seen_nodes.intersection(nodes)
            or seen_lines.intersection(lines)
        ):
            _invalid_state()
        seen_nodes.update(nodes)
        seen_lines.update(lines)
    return state, graph


def _vertex_key(vertex: Vertex) -> tuple[str, str, str, int]:
    if isinstance(vertex, str):
        return "node", vertex, "", -1
    return "line", vertex[0], vertex[1], vertex[2]


def _point(left: Vertex, right: Vertex) -> tuple[str, int, int]:
    assert isinstance(left, tuple) and isinstance(right, tuple)
    axes = {left[1]: left, right[1]: right}
    return left[0], axes["X"][2], axes["Y"][2]


def _adjacency(graph: ResourceGraph) -> tuple[
    dict[Vertex, list[tuple[Vertex, int, tuple[str, int, int] | None]]],
    dict[Vertex, list[Vertex]],
]:
    adjacent: dict[Vertex, list[tuple[Vertex, int, tuple[str, int, int] | None]]] = {
        vertex: [] for vertex in graph.vertices
    }
    fixed: dict[Vertex, list[Vertex]] = {vertex: [] for vertex in graph.vertices}
    for left, right in graph.fixed_edges:
        adjacent[left].append((right, 0, None))
        adjacent[right].append((left, 0, None))
        fixed[left].append(right)
        fixed[right].append(left)
    for left, right in graph.crosspoint_edges:
        point = _point(left, right)
        adjacent[left].append((right, 1, point))
        adjacent[right].append((left, 1, point))
    for neighbors in adjacent.values():
        neighbors.sort(key=lambda item: (_vertex_key(item[0]), item[1]))
    for neighbors in fixed.values():
        neighbors.sort(key=_vertex_key)
    return adjacent, fixed


def _fixed_closure(
    starts: Iterable[Vertex], fixed: Mapping[Vertex, list[Vertex]], blocked: set[LineVertex]
) -> set[Vertex]:
    reached = set(starts)
    pending = deque(sorted(reached, key=_vertex_key))
    while pending:
        vertex = pending.popleft()
        for neighbor in fixed[vertex]:
            if neighbor in reached or (isinstance(neighbor, tuple) and neighbor in blocked):
                continue
            reached.add(neighbor)
            pending.append(neighbor)
    return reached


def _shortest_attachment(
    tree: set[Vertex],
    targets: set[str],
    adjacent: Mapping[Vertex, list[tuple[Vertex, int, tuple[str, int, int] | None]]],
    blocked: set[LineVertex],
) -> tuple[str, list[Vertex], list[tuple[str, int, int]]] | None:
    best: dict[Vertex, tuple[int, tuple[tuple[str, str, str, int], ...]]] = {}
    paths: dict[Vertex, tuple[list[Vertex], list[tuple[str, int, int]]]] = {}
    queue: list[tuple[int, tuple[tuple[str, str, str, int], ...], Vertex]] = []
    for source in sorted(tree, key=_vertex_key):
        rank = (0, (_vertex_key(source),))
        best[source] = rank
        paths[source] = ([source], [])
        heapq.heappush(queue, (rank[0], rank[1], source))

    while queue:
        cost, path_key, vertex = heapq.heappop(queue)
        if best.get(vertex) != (cost, path_key):
            continue
        if isinstance(vertex, str) and vertex in targets:
            vertices, points = paths[vertex]
            return vertex, vertices, points
        vertices, points = paths[vertex]
        for neighbor, edge_cost, point in adjacent[vertex]:
            if isinstance(neighbor, tuple) and neighbor in blocked:
                continue
            candidate_key = path_key + (_vertex_key(neighbor),)
            candidate = (cost + edge_cost, candidate_key)
            if neighbor in best and best[neighbor] <= candidate:
                continue
            best[neighbor] = candidate
            paths[neighbor] = (
                vertices + [neighbor],
                points + ([] if point is None else [point]),
            )
            heapq.heappush(queue, (candidate[0], candidate[1], neighbor))
    return None


def _owners(state: dict[str, Any]) -> tuple[dict[str, str], dict[LineVertex, str]]:
    node_owners: dict[str, str] = {}
    line_owners: dict[LineVertex, str] = {}
    for net_id, net in state["nets"].items():
        node_owners.update({node: net_id for node in net["nodes"]})
        line_owners.update({_line(value): net_id for value in net["lines"]})
    return node_owners, line_owners


def _node_lines(graph: ResourceGraph) -> dict[str, LineVertex]:
    result: dict[str, LineVertex] = {}
    for left, right in graph.fixed_edges:
        if isinstance(left, tuple) and isinstance(right, str):
            result[right] = left
    return result


def _require_all_permanently_joined(missing: Iterable[str]) -> None:
    missing_nodes = sorted(set(missing))
    if missing_nodes:
        raise RouteError(
            "NODES_PERMANENTLY_JOINED",
            "all nodes permanently joined to the net must be included",
            missing=missing_nodes,
        )


def crossbar_create_net(
    state: dict[str, Any], net_id: str, nodes: Iterable[str]
) -> dict[str, Any]:
    """Route a new net with a deterministic greedy Steiner approximation."""

    state, graph = _validated_state(state)
    if not isinstance(net_id, str) or not net_id.strip():
        raise RouteError("INVALID_NET_ID", "net id must be a non-empty string")
    if net_id in state["nets"]:
        raise RouteError("NET_ALREADY_EXISTS", f"net {net_id!r} already exists", net=net_id)
    supplied = list(nodes)
    members = sorted(set(supplied)) if all(isinstance(node, str) for node in supplied) else []
    known_nodes = {vertex for vertex in graph.vertices if isinstance(vertex, str)}
    unknown = sorted(
        {str(node) for node in supplied if not isinstance(node, str) or node not in known_nodes}
    )
    if unknown:
        raise RouteError(
            "NODE_NOT_IN_TOPOLOGY", "one or more nodes are not in the topology", nodes=unknown
        )
    if len(members) < 2:
        raise RouteError("NET_TOO_SMALL", "a net must contain at least two distinct nodes")

    adjacent, fixed = _adjacency(graph)
    fixed_component = _fixed_closure(members, fixed, set())
    _require_all_permanently_joined(
        vertex
        for vertex in fixed_component
        if isinstance(vertex, str) and vertex not in members
    )

    node_owners, line_owners = _owners(state)
    attachments = _node_lines(graph)
    owners = {
        node: node_owners.get(node, line_owners.get(attachments[node]))
        for node in members
        if node in node_owners or attachments[node] in line_owners
    }
    if owners:
        raise RouteError(
            "NODES_ALREADY_IN_OTHER_NET",
            "one or more nodes already belong to another net",
            owners={node: owners[node] for node in sorted(owners)},
        )

    blocked = set(line_owners)
    tree = _fixed_closure([members[0]], fixed, blocked)
    remaining = set(members) - {vertex for vertex in tree if isinstance(vertex, str)}
    crosspoints: set[tuple[str, int, int]] = set()
    while remaining:
        route = _shortest_attachment(tree, remaining, adjacent, blocked)
        if route is None:
            unreachable = min(remaining)
            raise RouteError(
                "NO_FREE_PATH",
                "no path in current free resources; not proven impossible",
                member=unreachable,
                resource_reason="RESOURCE_EXHAUSTED",
            )
        _, path, points = route
        tree.update(_fixed_closure(path, fixed, blocked))
        crosspoints.update(points)
        remaining -= {vertex for vertex in tree if isinstance(vertex, str)}

    lines = sorted(vertex for vertex in tree if isinstance(vertex, tuple))
    _require_all_permanently_joined(
        node for node, line in attachments.items() if line in tree and node not in members
    )
    updated = copy.deepcopy(state)
    updated["nets"][net_id] = {
        "crosspoints": [list(point) for point in sorted(crosspoints)],
        "lines": [list(line) for line in lines],
        "nodes": members,
    }
    all_points = [
        point
        for net in updated["nets"].values()
        for point in net["crosspoints"]
    ]
    assignments = {
        name: net["nodes"] for name, net in updated["nets"].items()
    }
    shorts = find_shorts(graph, all_points, assignments)
    if shorts:
        raise RouteError(
            "INTERNAL_SHORT",
            "routed nets contain an unexpected electrical short",
            shorts=shorts,
        )
    return updated


def crossbar_delete_net(state: dict[str, Any], net_id: str) -> dict[str, Any]:
    """Return a new state without ``net_id``, freeing its lines and switches."""

    state, _ = _validated_state(state)
    if net_id not in state["nets"]:
        raise RouteError("NET_NOT_FOUND", f"net {net_id!r} does not exist", net=net_id)
    updated = copy.deepcopy(state)
    del updated["nets"][net_id]
    return updated


def _net_view(net_id: str, net: dict[str, Any]) -> dict[str, Any]:
    points = copy.deepcopy(net["crosspoints"])
    return {
        "crosspoints": points,
        "lines": copy.deepcopy(net["lines"]),
        "net": net_id,
        "nodes": list(net["nodes"]),
        "path_hops": len(points),
    }


def crossbar_query(state: dict[str, Any], net_id: str | None = None) -> dict[str, Any]:
    """Return stable per-net paths or aggregate chip-line occupancy."""

    state, graph = _validated_state(state)
    if net_id is not None:
        if net_id not in state["nets"]:
            raise RouteError("NET_NOT_FOUND", f"net {net_id!r} does not exist", net=net_id)
        return _net_view(net_id, state["nets"][net_id])
    nets = [_net_view(name, state["nets"][name]) for name in sorted(state["nets"])]
    used = sum(len(net["lines"]) for net in state["nets"].values())
    total = len(graph.exclusive_lines)
    return {
        "nets": nets,
        "occupancy": {"free": total - used, "total": total, "used": used},
        "version": state["version"],
    }
