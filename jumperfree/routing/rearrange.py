"""Bounded, deterministic local rearrangement for crossbar routes."""

from __future__ import annotations

import copy
import heapq
from itertools import combinations
from typing import Any, Iterable

from .crossbar import Vertex, build_resource_graph, find_shorts
from .model import RouteError
from .steiner import crossbar_create_net


_CANDIDATE_FAILURES = {
    "NET_ALREADY_EXISTS",
    "NODES_ALREADY_IN_OTHER_NET",
    "NODES_PERMANENTLY_JOINED",
    "NO_FREE_PATH",
}


def _validate_budget(name: str, value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RouteError("INVALID_REARRANGE_BUDGET", f"{name} must be a non-negative integer")
    return value


def _vertex_key(vertex: Vertex) -> tuple[str, str, str, int]:
    if isinstance(vertex, str):
        return "node", vertex, "", -1
    return "line", vertex[0], vertex[1], vertex[2]


def _rank_candidate_nets(state: dict[str, Any], members: Iterable[str]) -> list[str]:
    """Rank nets by occupancy on a cheap relaxed path union for ``members``."""

    graph = build_resource_graph(state["topology"])
    parent = {vertex: vertex for vertex in graph.vertices}

    def find(vertex: Vertex) -> Vertex:
        while parent[vertex] != vertex:
            parent[vertex] = parent[parent[vertex]]
            vertex = parent[vertex]
        return vertex

    def union(left: Vertex, right: Vertex) -> None:
        left_root, right_root = find(left), find(right)
        if left_root == right_root:
            return
        if _vertex_key(left_root) > _vertex_key(right_root):
            left_root, right_root = right_root, left_root
        parent[right_root] = left_root

    for left, right in graph.fixed_edges:
        union(left, right)

    component = {vertex: _vertex_key(find(vertex)) for vertex in graph.vertices}
    owners: dict[tuple[str, str, str, int], set[str]] = {}
    for existing_id, net in state["nets"].items():
        for value in net["lines"]:
            line = tuple(value)
            owners.setdefault(component[line], set()).add(existing_id)

    adjacency = {key: set() for key in set(component.values())}
    for left, right in graph.crosspoint_edges:
        left_component, right_component = component[left], component[right]
        if left_component != right_component:
            adjacency[left_component].add(right_component)
            adjacency[right_component].add(left_component)

    requested = sorted(set(members))
    source = component[requested[0]]
    best = {source: (0, 0)}
    predecessor: dict[tuple[str, str, str, int], tuple[str, str, str, int]] = {}
    queue = [(0, 0, source)]
    while queue:
        occupied, hops, current = heapq.heappop(queue)
        if best.get(current) != (occupied, hops):
            continue
        for neighbor in sorted(adjacency[current]):
            candidate = (occupied + bool(owners.get(neighbor)), hops + 1)
            if neighbor in best and best[neighbor] <= candidate:
                continue
            best[neighbor] = candidate
            predecessor[neighbor] = current
            heapq.heappush(queue, (*candidate, neighbor))

    path_union: set[tuple[str, str, str, int]] = set()
    for member in requested:
        current = component[member]
        while current not in path_union:
            path_union.add(current)
            if current == source:
                break
            current = predecessor[current]

    scores = {existing_id: 0 for existing_id in state["nets"]}
    for key in path_union:
        for existing_id in owners.get(key, ()):
            scores[existing_id] += 1
    return sorted(scores, key=lambda existing_id: (-scores[existing_id], existing_id))


def _without_nets(state: dict[str, Any], net_ids: Iterable[str]) -> dict[str, Any]:
    reduced = copy.deepcopy(state)
    for net_id in net_ids:
        del reduced["nets"][net_id]
    return reduced


def _move_count(before: dict[str, Any], after: dict[str, Any], net_ids: Iterable[str]) -> int:
    moves = 0
    for net_id in net_ids:
        old, new = before["nets"][net_id], after["nets"][net_id]
        for resource in ("crosspoints", "lines"):
            moves += len({tuple(item) for item in old[resource]} ^ {tuple(item) for item in new[resource]})
    return moves


def _topology_separates_members(state: dict[str, Any], members: Iterable[str]) -> bool:
    """Return true only when the complete resource graph proves disconnection."""

    graph = build_resource_graph(state["topology"])
    adjacency: dict[Vertex, list[Vertex]] = {vertex: [] for vertex in graph.vertices}
    for left, right in (*graph.fixed_edges, *graph.crosspoint_edges):
        adjacency[left].append(right)
        adjacency[right].append(left)
    requested = sorted(set(members))
    reached: set[Vertex] = {requested[0]}
    pending: list[Vertex] = [requested[0]]
    while pending:
        for neighbor in adjacency[pending.pop()]:
            if neighbor not in reached:
                reached.add(neighbor)
                pending.append(neighbor)
    return any(member not in reached for member in requested[1:])


def _assert_no_shorts(state: dict[str, Any]) -> None:
    points = [point for net in state["nets"].values() for point in net["crosspoints"]]
    assignments = {net_id: net["nodes"] for net_id, net in state["nets"].items()}
    shorts = find_shorts(state["topology"], points, assignments)
    if shorts:
        raise RouteError(
            "INTERNAL_SHORT",
            "rearranged nets contain an unexpected electrical short",
            shorts=shorts,
        )


def _failure(
    reason: str,
    max_moves: int,
    max_states: int,
    max_rip_nets: int,
    explored: int,
) -> RouteError:
    message = (
        "members are disconnected by invariant topology constraints"
        if reason == "UNROUTABLE_INVARIANT"
        else "no path in current free resources within rearrange budget; not proven impossible"
    )
    return RouteError(
        "NO_FREE_PATH",
        message,
        max_moves=max_moves,
        max_rip_nets=max_rip_nets,
        max_states=max_states,
        rearrange_attempted=True,
        resource_reason=reason,
        states_explored=explored,
    )


def crossbar_create_net_with_rearrange(
    state: dict[str, Any],
    net_id: str,
    nodes: Iterable[str],
    *,
    max_moves: int = 8,
    max_states: int = 2000,
    max_rip_nets: int = 3,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create a net, moving a bounded number of existing nets within budgets.

    ``moves`` counts the line and crosspoint symmetric differences of existing
    nets.  The unavoidable resources used by the newly-created net are not a
    rearrangement, which is why a normal create reports zero moves.
    """

    move_budget = _validate_budget("max_moves", max_moves)
    state_budget = _validate_budget("max_states", max_states)
    rip_budget = _validate_budget("max_rip_nets", max_rip_nets)
    supplied = list(nodes)
    try:
        routed = crossbar_create_net(state, net_id, supplied)
    except RouteError as exc:
        if exc.code != "NO_FREE_PATH":
            raise
    else:
        return routed, {
            "moves": 0,
            "rearrange_attempted": False,
            "rearranged_nets": [],
            "states_explored": 0,
        }

    if _topology_separates_members(state, supplied):
        raise _failure(
            "UNROUTABLE_INVARIANT", move_budget, state_budget, rip_budget, 0
        )

    if state_budget == 0 or rip_budget == 0:
        raise _failure("RESOURCE_EXHAUSTED", move_budget, state_budget, rip_budget, 0)

    # The relaxed graph probe is advisory: it ranks likely blockers but never
    # excludes a net.  Combination generation remains lazy so max_states is a
    # hard bound on routing trials and their storage.
    candidate_ids = _rank_candidate_nets(state, supplied)
    explored = 0
    for size in range(1, min(rip_budget, len(candidate_ids)) + 1):
        for selected in combinations(candidate_ids, size):
            if explored >= state_budget:
                raise _failure(
                    "RESOURCE_EXHAUSTED",
                    move_budget,
                    state_budget,
                    rip_budget,
                    explored,
                )
            explored += 1
            working = _without_nets(state, selected)
            try:
                working = crossbar_create_net(working, net_id, supplied)
                for existing_id in sorted(selected):
                    working = crossbar_create_net(
                        working, existing_id, state["nets"][existing_id]["nodes"]
                    )
            except RouteError as exc:
                if exc.code in _CANDIDATE_FAILURES:
                    continue
                raise
            moves = _move_count(state, working, selected)
            if moves > move_budget:
                continue
            _assert_no_shorts(working)
            changed = [
                existing_id
                for existing_id in sorted(selected)
                if _move_count(state, working, [existing_id])
            ]
            return working, {
                "moves": moves,
                "rearrange_attempted": True,
                "rearranged_nets": changed,
                "states_explored": explored,
            }

    raise _failure(
        "RESOURCE_EXHAUSTED", move_budget, state_budget, rip_budget, explored
    )
