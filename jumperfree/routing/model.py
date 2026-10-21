"""Deterministic stage-1 routing over a simulated complete resource graph."""

from __future__ import annotations

import copy
import hashlib
from itertools import combinations
from typing import Any, Iterable

STATE_VERSION = 1


class RouteError(ValueError):
    """A structured routing failure that is safe to expose through the CLI."""

    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {"code": self.code, "message": self.message}
        if self.details:
            result["details"] = self.details
        return result


def init_state(nodes: int, seed: int = 0, capacity: int = 1) -> dict[str, Any]:
    """Create an empty routing state for nodes ``0..nodes-1``."""
    if nodes < 2:
        raise RouteError("INVALID_NODE_COUNT", "node count must be at least 2", nodes=nodes)
    if capacity < 0:
        raise RouteError(
            "INVALID_CAPACITY", "edge capacity must be zero or greater", capacity=capacity
        )
    return {
        "capacity": capacity,
        "nets": {},
        "node_count": nodes,
        "seed": seed,
        "version": STATE_VERSION,
    }


def _validate_state(state: dict[str, Any]) -> None:
    if (
        not isinstance(state, dict)
        or state.get("version") != STATE_VERSION
        or not isinstance(state.get("node_count"), int)
        or not isinstance(state.get("capacity"), int)
        or not isinstance(state.get("seed"), int)
        or not isinstance(state.get("nets"), dict)
    ):
        raise RouteError("INVALID_STATE", "routing state has an invalid shape or version")


def _edge_key(edge: dict[str, int]) -> tuple[int, int, int]:
    return edge["u"], edge["v"], edge["slot"]


def _occupied(state: dict[str, Any]) -> set[tuple[int, int, int]]:
    return {
        _edge_key(edge)
        for net in state["nets"].values()
        for edge in net["edges"]
    }


def _edge_rank(seed: int, u: int, v: int, slot: int) -> tuple[bytes, int, int, int]:
    digest = hashlib.sha256(f"{seed}:{u}:{v}:{slot}".encode("ascii")).digest()
    return digest, u, v, slot


def _spanning_tree(state: dict[str, Any], members: list[int]) -> list[dict[str, int]]:
    occupied = _occupied(state)
    candidates = [
        (u, v, slot)
        for u, v in combinations(members, 2)
        for slot in range(state["capacity"])
        if (u, v, slot) not in occupied
    ]
    candidates.sort(key=lambda edge: _edge_rank(state["seed"], *edge))

    parent = {node: node for node in members}

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    selected: list[dict[str, int]] = []
    for u, v, slot in candidates:
        root_u, root_v = find(u), find(v)
        if root_u == root_v:
            continue
        parent[root_v] = root_u
        selected.append({"slot": slot, "u": u, "v": v})
        if len(selected) == len(members) - 1:
            return sorted(selected, key=_edge_key)
    raise RouteError(
        "NO_FREE_PATH",
        "free simulated resources cannot connect every node; stage 1 does not rearrange nets",
        available_edges=len(candidates),
        resource_reason="RESOURCE_EXHAUSTED",
    )


def create_net(state: dict[str, Any], net_id: str, nodes: Iterable[int]) -> dict[str, Any]:
    """Return a new state with ``net_id`` connected by free simulated resources."""
    _validate_state(state)
    if not isinstance(net_id, str) or not net_id.strip():
        raise RouteError("INVALID_NET_ID", "net id must be a non-empty string")
    if net_id in state["nets"]:
        raise RouteError("NET_ALREADY_EXISTS", f"net {net_id!r} already exists", net=net_id)

    supplied = list(nodes)
    non_integers = [node for node in supplied if not isinstance(node, int)]
    if non_integers:
        raise RouteError(
            "NODE_OUT_OF_RANGE",
            f"nodes must be integers in 0..{state['node_count'] - 1}",
            nodes=non_integers,
        )
    members = sorted(set(supplied))
    if len(members) < 2:
        raise RouteError("NET_TOO_SMALL", "a net must contain at least two distinct nodes")
    invalid = [node for node in members if not 0 <= node < state["node_count"]]
    if invalid:
        raise RouteError(
            "NODE_OUT_OF_RANGE",
            f"nodes must be integers in 0..{state['node_count'] - 1}",
            nodes=invalid,
        )

    owners = {
        node: existing_id
        for existing_id, net in state["nets"].items()
        for node in net["nodes"]
        if node in members
    }
    if owners:
        raise RouteError(
            "NODES_ALREADY_IN_OTHER_NET",
            "one or more nodes already belong to another net",
            owners={str(node): owners[node] for node in sorted(owners)},
        )

    updated = copy.deepcopy(state)
    updated["nets"][net_id] = {
        "edges": _spanning_tree(state, members),
        "nodes": members,
    }
    return updated


def delete_net(state: dict[str, Any], net_id: str) -> dict[str, Any]:
    """Return a new state without ``net_id``, freeing all of its resources."""
    _validate_state(state)
    if net_id not in state["nets"]:
        raise RouteError("NET_NOT_FOUND", f"net {net_id!r} does not exist", net=net_id)
    updated = copy.deepcopy(state)
    del updated["nets"][net_id]
    return updated


def _connected(net: dict[str, Any]) -> bool:
    reached = {net["nodes"][0]}
    changed = True
    while changed:
        changed = False
        for edge in net["edges"]:
            if edge["u"] in reached and edge["v"] not in reached:
                reached.add(edge["v"])
                changed = True
            if edge["v"] in reached and edge["u"] not in reached:
                reached.add(edge["u"])
                changed = True
    return reached == set(net["nodes"])


def _net_view(net_id: str, net: dict[str, Any]) -> dict[str, Any]:
    return {
        "connected": _connected(net),
        "edges": sorted(copy.deepcopy(net["edges"]), key=_edge_key),
        "net": net_id,
        "nodes": list(net["nodes"]),
    }


def query_state(state: dict[str, Any], net_id: str | None = None) -> dict[str, Any]:
    """Return deterministic occupancy and connectivity information."""
    _validate_state(state)
    if net_id is not None:
        if net_id not in state["nets"]:
            raise RouteError("NET_NOT_FOUND", f"net {net_id!r} does not exist", net=net_id)
        return _net_view(net_id, state["nets"][net_id])

    nets = [_net_view(name, state["nets"][name]) for name in sorted(state["nets"])]
    used = sum(len(net["edges"]) for net in state["nets"].values())
    total = state["capacity"] * state["node_count"] * (state["node_count"] - 1) // 2
    return {
        "capacity": state["capacity"],
        "nets": nets,
        "node_count": state["node_count"],
        "occupancy": {"free": total - used, "total": total, "used": used},
        "seed": state["seed"],
        "version": state["version"],
    }


def list_nets(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Return all nets in stable identifier order."""
    return query_state(state)["nets"]
