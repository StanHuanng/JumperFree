"""Deterministic evaluation harness for the small crossbar routers.

The reference used here is deliberately local: it exhausts every choice of at
most two existing nets to rip up and every ordering in which those nets and the
new net can be greedily re-routed.  It is not an exhaustive search over switch
settings and therefore does not claim global optimality.
"""

from __future__ import annotations

import copy
import random
from itertools import combinations, permutations, product
from typing import Any, Callable, Iterable, Sequence

from .crossbar import build_resource_graph, find_shorts, small_topology
from .model import RouteError
from .rearrange import crossbar_create_net_with_rearrange
from .steiner import crossbar_create_net, crossbar_delete_net, crossbar_init_state

REFERENCE_MAX_NODES = 8
REFERENCE_MAX_NETS = 4
REFERENCE_MAX_RIPPED_NETS = 2
_REFERENCE_CANDIDATE_FAILURES = {
    "NET_ALREADY_EXISTS",
    "NODES_ALREADY_IN_OTHER_NET",
    "NODES_PERMANENTLY_JOINED",
    "NO_FREE_PATH",
}
_WORKLOAD_CREATE_FAILURES = {
    "NODES_ALREADY_IN_OTHER_NET",
    "NODES_PERMANENTLY_JOINED",
    "NO_FREE_PATH",
}

Router = Callable[[dict[str, Any], str, Iterable[str]], tuple[dict[str, Any], dict[str, Any]]]


def _plain_router(
    state: dict[str, Any], net_id: str, nodes: Iterable[str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    return crossbar_create_net(state, net_id, nodes), {"moves": 0, "states_explored": 0}


def _rearrange_router(max_moves: int, max_states: int, max_rip_nets: int) -> Router:
    def route(
        state: dict[str, Any], net_id: str, nodes: Iterable[str]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        return crossbar_create_net_with_rearrange(
            state,
            net_id,
            nodes,
            max_moves=max_moves,
            max_states=max_states,
            max_rip_nets=max_rip_nets,
        )

    return route


def exhaustive_reference(
    state: dict[str, Any],
    net_id: str,
    nodes: Iterable[str],
    *,
    max_ripped_nets: int = REFERENCE_MAX_RIPPED_NETS,
) -> bool:
    """Return whether the request is found in the exhaustive local neighborhood.

    For every subset of up to ``max_ripped_nets`` existing nets, all orderings
    of the new request and ripped nets are tried.  Each individual route still
    uses the deterministic Steiner approximation.
    """

    requested = list(nodes)
    jobs = {name: list(net["nodes"]) for name, net in state["nets"].items()}
    existing = sorted(jobs)
    for size in range(min(max_ripped_nets, len(existing)) + 1):
        for ripped in combinations(existing, size):
            base = copy.deepcopy(state)
            for name in ripped:
                del base["nets"][name]
            pending = ((net_id, requested),) + tuple((name, jobs[name]) for name in ripped)
            for ordering in permutations(pending):
                candidate = base
                try:
                    for name, members in ordering:
                        candidate = crossbar_create_net(candidate, name, members)
                except RouteError as exc:
                    if exc.code in _REFERENCE_CANDIDATE_FAILURES:
                        continue
                    raise
                return True
    return False


def check_constraints(state: dict[str, Any]) -> dict[str, int]:
    """Count electrical shorts and violations of exclusive line ownership."""

    lines = [
        tuple(line)
        for net in state["nets"].values()
        for line in net["lines"]
    ]
    points = [
        point
        for net in state["nets"].values()
        for point in net["crosspoints"]
    ]
    assignments = {name: net["nodes"] for name, net in state["nets"].items()}
    shorts = find_shorts(build_resource_graph(state["topology"]), points, assignments)
    return {
        "line_exclusivity_violations": len(lines) - len(set(lines)),
        "shorts_found": len(shorts),
    }


def aggregate_metrics(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-create observations into stable JSON metric fields."""

    attempts = len(records)
    successes = sum(bool(record["accepted"]) for record in records)
    compared = [record for record in records if record["reference"] is not None]
    agreements = sum(
        bool(record["accepted"]) == bool(record["reference"]) for record in compared
    )
    divisor = attempts or 1
    comparison_divisor = len(compared) or 1
    return {
        "attempts": attempts,
        "success_rate": successes / divisor,
        "avg_moves": sum(int(record["moves"]) for record in records) / divisor,
        "avg_states": sum(int(record["states"]) for record in records) / divisor,
        "timeouts": 0,
        "budget_misses": sum(bool(record["budget_miss"]) for record in records),
        "timeouts_or_budget_misses": sum(bool(record["budget_miss"]) for record in records),
        "shorts_found": sum(int(record["shorts_found"]) for record in records),
        "line_exclusivity_violations": sum(
            int(record["line_exclusivity_violations"]) for record in records
        ),
        "compared_to_reference": {
            "cases": len(compared),
            "agreement_rate": agreements / comparison_divisor,
        },
    }


def _workload(rng: random.Random, nodes: Sequence[str], trials: int) -> list[tuple[str, str, list[str]]]:
    operations: list[tuple[str, str, list[str]]] = []
    live: dict[str, list[str]] = {}
    next_id = 0
    for _ in range(trials):
        reserved = {node for members in live.values() for node in members}
        available = sorted(set(nodes) - reserved)
        if live and (len(available) < 2 or rng.random() < 0.3):
            net_id = rng.choice(sorted(live))
            del live[net_id]
            operations.append(("delete", net_id, []))
        else:
            net_id = f"net-{next_id}"
            next_id += 1
            size = rng.randint(2, min(4, len(available)))
            members = sorted(rng.sample(available, size))
            operations.append(("create", net_id, members))
            live[net_id] = members
    return operations


def _run_router(
    topology: dict[str, Any],
    operations: Sequence[tuple[str, str, list[str]]],
    router: Router,
    *,
    reference_max_nodes: int,
    reference_max_nets: int,
) -> list[dict[str, Any]]:
    state = crossbar_init_state(topology)
    records: list[dict[str, Any]] = []
    node_count = len(topology["node_attachments"])
    for operation, net_id, nodes in operations:
        if operation == "delete":
            if net_id in state["nets"]:
                state = crossbar_delete_net(state, net_id)
                constraints = check_constraints(state)
                if any(constraints.values()):
                    raise AssertionError("router state violated electrical constraints after delete")
            continue

        reference = None
        if node_count <= reference_max_nodes and len(state["nets"]) <= reference_max_nets:
            reference = exhaustive_reference(state, net_id, nodes)
        accepted = False
        moves = 0
        states = 0
        constraints = {"line_exclusivity_violations": 0, "shorts_found": 0}
        pre_create = copy.deepcopy(state)
        try:
            updated, stats = router(state, net_id, nodes)
        except RouteError as exc:
            if exc.code not in _WORKLOAD_CREATE_FAILURES:
                raise
            state = pre_create
            states = int(exc.details.get("states_explored", 0))
        else:
            moves = int(stats.get("moves", 0))
            states = int(stats.get("states_explored", 0))
            constraints = check_constraints(updated)
            if not any(constraints.values()):
                accepted = True
                state = updated
            else:
                state = pre_create
        records.append({
            "accepted": accepted,
            "budget_miss": reference is True and not accepted,
            "line_exclusivity_violations": constraints["line_exclusivity_violations"],
            "moves": moves,
            "reference": reference,
            "shorts_found": constraints["shorts_found"],
            "states": states,
        })
    return records


def run_evaluation(
    *,
    seed: int,
    trials: int,
    max_moves: int = 8,
    max_states: int = 2000,
    max_rip_nets: int = 3,
    reference_max_nodes: int = REFERENCE_MAX_NODES,
    reference_max_nets: int = REFERENCE_MAX_NETS,
) -> dict[str, Any]:
    """Run both routers over every requested small-topology dimension."""

    if trials < 0:
        raise ValueError("trials must be non-negative")
    for name, value in (
        ("max_moves", max_moves),
        ("max_states", max_states),
        ("max_rip_nets", max_rip_nets),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise RouteError(
                "INVALID_REARRANGE_BUDGET",
                f"{name} must be a non-negative integer",
            )
    results: list[dict[str, Any]] = []
    rng = random.Random(seed)
    routers = (
        ("steiner", _plain_router),
        ("rearrange", _rearrange_router(max_moves, max_states, max_rip_nets)),
    )
    for chips, x_lines, y_lines in product((1, 2, 3), (2, 3), (2, 3)):
        topology = small_topology(chips, x_lines, y_lines)
        nodes = sorted(topology["node_attachments"])
        operations = _workload(rng, nodes, trials)
        for router_name, router in routers:
            metrics = aggregate_metrics(
                _run_router(
                    topology,
                    operations,
                    router,
                    reference_max_nodes=reference_max_nodes,
                    reference_max_nets=reference_max_nets,
                )
            )
            results.append({
                "router": router_name,
                "seed": seed,
                "topology": {
                    "chips": chips,
                    "node_count": len(nodes),
                    "x_lines": x_lines,
                    "y_lines": y_lines,
                },
                **metrics,
            })
    return {
        "reference": {
            "description": "exhaustive within its separately bounded two-net rearrangement neighborhood; not a global optimum",
            "max_existing_nets": reference_max_nets,
            "max_nodes": reference_max_nodes,
            "max_ripped_nets": REFERENCE_MAX_RIPPED_NETS,
        },
        "results": results,
        "seed": seed,
        "trials": trials,
    }
