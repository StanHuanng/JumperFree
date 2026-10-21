"""Deterministic safety evaluation on the synthetic V5-scale topology."""

from __future__ import annotations

import copy
import hashlib
import json
import random
from typing import Any, Callable, Iterable, Sequence

from .crossbar import Vertex, build_resource_graph, find_shorts
from .model import RouteError
from .rearrange import crossbar_create_net_with_rearrange
from .steiner import crossbar_create_net, crossbar_delete_net, crossbar_init_state
from .v5_scale import v5_scale_topology

DEFAULT_MAX_MOVES = 64
DEFAULT_MAX_STATES = 32
DEFAULT_MAX_RIP_NETS = 3

ScaleOperation = dict[str, Any]
ScaleRouter = Callable[
    [dict[str, Any], str, Iterable[str], ScaleOperation],
    tuple[dict[str, Any], dict[str, Any]],
]

_EXPECTED_CREATE_FAILURES = {
    "NET_ALREADY_EXISTS",
    "NODES_ALREADY_IN_OTHER_NET",
    "NODES_PERMANENTLY_JOINED",
    "NO_FREE_PATH",
}
_EXPECTED_DELETE_FAILURES = {"NET_NOT_FOUND"}


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _operation(action: str, net_id: str, members: Sequence[str], case: str) -> ScaleOperation:
    return {
        "action": action,
        "case": case,
        "members": sorted(set(members)),
        "net": net_id,
    }


def make_scale_workload(*, seed: int = 7, trials: int = 60) -> list[ScaleOperation]:
    """Build a stable operation stream containing fixed probes plus seeded churn."""

    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("seed must be an integer")
    if not isinstance(trials, int) or isinstance(trials, bool) or trials < 0:
        raise ValueError("trials must be a non-negative integer")

    fixed = [
        _operation("create", "old0", ["BB01", "BB02"], "congestion_setup"),
        _operation("create", "old1", ["BB03", "BB04"], "congestion_setup"),
        {
            **_operation(
                "create", "budget-zero", ["BB05", "SF_L"], "zero_state_budget_probe"
            ),
            "budgets": {"max_moves": 64, "max_rip_nets": 3, "max_states": 0},
        },
        {
            **_operation(
                "create", "budget-moves", ["BB05", "SF_L"],
                "insufficient_move_budget_probe",
            ),
            "budgets": {"max_moves": 6, "max_rip_nets": 3, "max_states": 32},
        },
        _operation(
            "create", "congestion", ["BB05", "SF_L"], "congestion_recovery"
        ),
        _operation("delete", "congestion", [], "delete_reuse"),
        _operation("delete", "old0", [], "delete_reuse"),
        _operation("delete", "old1", [], "delete_reuse"),
        _operation("create", "local", ["BB01", "BB02"], "fixed_local"),
        _operation("delete", "local", [], "delete_reuse"),
        _operation("create", "span", ["BB01", "SF_L"], "fixed_12_chip"),
        _operation("delete", "span", [], "delete_reuse"),
        _operation(
            "create", "multi", ["BB01", "BB29", "SF_L"], "fixed_multi_terminal"
        ),
        _operation("delete", "multi", [], "delete_reuse"),
    ]
    operations = fixed[:trials]
    if len(operations) == trials:
        return operations

    endpoints = sorted(v5_scale_topology()["node_attachments"])
    rng = random.Random(seed)
    live: dict[str, list[str]] = {}
    next_id = 0
    while len(operations) < trials:
        reserved = {member for members in live.values() for member in members}
        available = [node for node in endpoints if node not in reserved]
        if live and (len(available) < 2 or rng.random() < 0.3):
            net_id = rng.choice(sorted(live))
            del live[net_id]
            operations.append(_operation("delete", net_id, [], "seeded_churn"))
            continue
        net_id = f"churn-{next_id:03d}"
        next_id += 1
        size = rng.randint(2, min(4, len(available)))
        members = sorted(rng.sample(available, size))
        live[net_id] = members
        operations.append(_operation("create", net_id, members, "seeded_churn"))
    return operations


def _closed_edge(point: Sequence[object]) -> tuple[Vertex, Vertex]:
    left: Vertex = (str(point[0]), "X", int(point[1]))
    right: Vertex = (str(point[0]), "Y", int(point[2]))
    return left, right


def assert_scale_state(state: dict[str, Any]) -> dict[str, int]:
    """Assert shorts, exclusive ownership, connectivity, and endpoint isolation."""

    graph = build_resource_graph(state["topology"])
    lines = [tuple(line) for net in state["nets"].values() for line in net["lines"]]
    if len(lines) != len(set(lines)):
        raise AssertionError("exclusive chip line is owned by more than one net")

    points = [point for net in state["nets"].values() for point in net["crosspoints"]]
    assignments = {net_id: net["nodes"] for net_id, net in state["nets"].items()}
    shorts = find_shorts(graph, points, assignments)
    if shorts:
        raise AssertionError(f"electrical short detected: {shorts}")

    parent = {vertex: vertex for vertex in graph.vertices}

    def find(vertex: Vertex) -> Vertex:
        while parent[vertex] != vertex:
            parent[vertex] = parent[parent[vertex]]
            vertex = parent[vertex]
        return vertex

    def union(left: Vertex, right: Vertex) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left, right in graph.fixed_edges:
        union(left, right)
    for point in points:
        union(*_closed_edge(point))

    external = [vertex for vertex in graph.vertices if isinstance(vertex, str)]
    for net_id, net in sorted(state["nets"].items()):
        members = list(net["nodes"])
        roots = {find(member) for member in members}
        if len(roots) != 1:
            raise AssertionError(f"declared members of {net_id} are not electrically connected")
        root = next(iter(roots))
        joined = sorted(node for node in external if find(node) == root)
        if joined != members:
            raise AssertionError(
                f"net {net_id} electrically joins undeclared endpoints: "
                f"declared={members}, joined={joined}"
            )
    return {
        "line_exclusivity_violations": 0,
        "shorts_found": 0,
        "undeclared_endpoint_joins": 0,
    }


def _plain_router(
    state: dict[str, Any],
    net_id: str,
    members: Iterable[str],
    operation: ScaleOperation | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    return crossbar_create_net(state, net_id, members), {
        "moves": 0,
        "states_explored": 0,
    }


def _bounded_router(max_moves: int, max_states: int, max_rip_nets: int) -> ScaleRouter:
    def route(
        state: dict[str, Any],
        net_id: str,
        members: Iterable[str],
        operation: ScaleOperation,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        budgets = operation.get("budgets", {})
        return crossbar_create_net_with_rearrange(
            state,
            net_id,
            members,
            max_moves=int(budgets.get("max_moves", max_moves)),
            max_states=int(budgets.get("max_states", max_states)),
            max_rip_nets=int(budgets.get("max_rip_nets", max_rip_nets)),
        )

    return route


def _replay(
    topology: dict[str, Any],
    operations: Sequence[ScaleOperation],
    router: ScaleRouter,
    *,
    max_moves: int,
    max_states: int,
) -> dict[str, Any]:
    state = crossbar_init_state(topology)
    outcomes: list[dict[str, Any]] = []
    peak_occupancy = 0
    successes = 0
    create_successes = 0
    create_failures = 0
    delete_successes = 0
    delete_missing = 0
    assert_scale_state(state)
    for index, operation in enumerate(operations):
        before = copy.deepcopy(state)
        old_memberships = {
            net_id: list(net["nodes"]) for net_id, net in state["nets"].items()
        }
        accepted = False
        failure: dict[str, object] | None = None
        moves = 0
        states = 0
        try:
            if operation["action"] == "create":
                updated, stats = router(
                    state, operation["net"], operation["members"], operation
                )
                moves = int(stats.get("moves", 0))
                states = int(stats.get("states_explored", 0))
            else:
                updated = crossbar_delete_net(state, operation["net"])
        except RouteError as exc:
            expected = (
                _EXPECTED_CREATE_FAILURES
                if operation["action"] == "create"
                else _EXPECTED_DELETE_FAILURES
            )
            if exc.code not in expected:
                raise
            if state != before:
                raise AssertionError("failed request was not atomic") from exc
            failure = exc.as_dict()
            states = int(exc.details.get("states_explored", 0))
            operation_max_states = int(
                operation.get("budgets", {}).get("max_states", max_states)
            )
            if states > operation_max_states:
                raise AssertionError("failed request exceeded the state budget") from exc
            assert_scale_state(state)
            if operation["action"] == "create":
                create_failures += 1
            else:
                delete_missing += 1
        else:
            if state != before:
                raise AssertionError("router mutated its input state")
            assert_scale_state(updated)
            expected_memberships = dict(old_memberships)
            if operation["action"] == "create":
                expected_memberships[operation["net"]] = operation["members"]
            else:
                del expected_memberships[operation["net"]]
            actual_memberships = {
                net_id: list(net["nodes"]) for net_id, net in updated["nets"].items()
            }
            if actual_memberships != expected_memberships:
                raise AssertionError("operation did not preserve declared net memberships")
            if operation["action"] == "create":
                actual_moves = 0
                for existing_id in old_memberships:
                    for resource in ("lines", "crosspoints"):
                        old_resources = {
                            tuple(item) for item in state["nets"][existing_id][resource]
                        }
                        new_resources = {
                            tuple(item) for item in updated["nets"][existing_id][resource]
                        }
                        actual_moves += len(old_resources ^ new_resources)
                operation_max_moves = int(
                    operation.get("budgets", {}).get("max_moves", max_moves)
                )
                operation_max_states = int(
                    operation.get("budgets", {}).get("max_states", max_states)
                )
                if (
                    actual_moves > operation_max_moves
                    or moves > operation_max_moves
                    or states > operation_max_states
                ):
                    raise AssertionError("router exceeded an evaluation budget")
                if actual_moves != moves:
                    raise AssertionError(
                        "router-reported moves do not match independently computed C_move"
                    )
            state = updated
            accepted = True
            successes += 1
            if operation["action"] == "create":
                create_successes += 1
            else:
                delete_successes += 1
        occupancy = sum(len(net["lines"]) for net in state["nets"].values())
        peak_occupancy = max(peak_occupancy, occupancy)
        outcomes.append({
            "accepted": accepted,
            "action": operation["action"],
            "budgets": operation.get("budgets"),
            "case": operation["case"],
            "failure": failure,
            "index": index,
            "members": operation["members"],
            "moves": moves,
            "net": operation["net"],
            "occupancy": occupancy,
            "states": states,
        })
    constraints = assert_scale_state(state)
    return {
        "constraints": constraints,
        "create_failures": create_failures,
        "create_successes": create_successes,
        "delete_missing": delete_missing,
        "delete_successes": delete_successes,
        "failures": len(operations) - successes,
        "outcomes": outcomes,
        "peak_occupancy": peak_occupancy,
        "route_digest": _digest(state),
        "successes": successes,
    }


def _budget(name: str, value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RouteError(
            "INVALID_REARRANGE_BUDGET", f"{name} must be a non-negative integer"
        )
    return value


def run_scale_evaluation(
    *,
    seed: int = 7,
    trials: int = 60,
    max_moves: int = DEFAULT_MAX_MOVES,
    max_states: int = DEFAULT_MAX_STATES,
    max_rip_nets: int = DEFAULT_MAX_RIP_NETS,
) -> dict[str, Any]:
    """Replay one scale workload independently through both routing modes."""

    move_budget = _budget("max_moves", max_moves)
    state_budget = _budget("max_states", max_states)
    rip_budget = _budget("max_rip_nets", max_rip_nets)
    operations = make_scale_workload(seed=seed, trials=trials)
    topology = v5_scale_topology()
    graph = build_resource_graph(topology)
    routers = (
        ("steiner", _plain_router),
        ("rearrange", _bounded_router(move_budget, state_budget, rip_budget)),
    )
    results = []
    for name, router in routers:
        result = _replay(
            topology,
            operations,
            router,
            max_moves=move_budget,
            max_states=state_budget,
        )
        results.append({"router": name, **result})
    return {
        "budgets": {
            "max_moves": move_budget,
            "max_rip_nets": rip_budget,
            "max_states": state_budget,
        },
        "comparisons": {
            "local_neighborhood_oracle": "unavailable_at_scale",
            "true_exhaustive_oracle": "unavailable_at_scale",
        },
        "results": results,
        "seed": seed,
        "topology": {
            "crosspoints": len(graph.crosspoint_edges),
            "exclusive_lines": len(graph.exclusive_lines),
            "external_endpoints": len(topology["node_attachments"]),
            "fixed_edges": len(graph.fixed_edges),
            "verified": False,
            "vertices": len(graph.vertices),
        },
        "trials": trials,
        "workload_digest": _digest(operations),
    }
