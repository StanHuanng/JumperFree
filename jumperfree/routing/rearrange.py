"""Bounded, deterministic local rearrangement for crossbar routes."""

from __future__ import annotations

import copy
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


def _failure(reason: str, max_moves: int, max_states: int, explored: int) -> RouteError:
    message = (
        "members are disconnected by invariant topology constraints"
        if reason == "UNROUTABLE_INVARIANT"
        else "no path in current free resources within rearrange budget; not proven impossible"
    )
    return RouteError(
        "NO_FREE_PATH",
        message,
        max_moves=max_moves,
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
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create a net, moving at most two existing nets within fixed budgets.

    ``moves`` counts the line and crosspoint symmetric differences of existing
    nets.  The unavoidable resources used by the newly-created net are not a
    rearrangement, which is why a normal create reports zero moves.
    """

    move_budget = _validate_budget("max_moves", max_moves)
    state_budget = _validate_budget("max_states", max_states)
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
        raise _failure("UNROUTABLE_INVARIANT", move_budget, state_budget, 0)

    if state_budget == 0:
        raise _failure("RESOURCE_EXHAUSTED", move_budget, state_budget, 0)

    # Generate trials on demand so both routing work and candidate storage stay
    # bounded.  A candidate's first route is also its blocking-resource probe.
    candidate_ids = sorted(state["nets"])
    explored = 0
    for size in range(1, min(2, len(candidate_ids)) + 1):
        for selected in combinations(candidate_ids, size):
            if explored >= state_budget:
                raise _failure("RESOURCE_EXHAUSTED", move_budget, state_budget, explored)
            explored += 1
            working = _without_nets(state, selected)
            try:
                working = crossbar_create_net(working, net_id, supplied)
                for existing_id in selected:
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
                for existing_id in selected
                if _move_count(state, working, [existing_id])
            ]
            return working, {
                "moves": moves,
                "rearrange_attempted": True,
                "rearranged_nets": changed,
                "states_explored": explored,
            }

    raise _failure("RESOURCE_EXHAUSTED", move_budget, state_budget, explored)
