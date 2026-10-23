"""Deterministic evaluation harness for the small crossbar routers.

Two reference oracles are used:

* ``exhaustive_reference`` — local neighborhood: rip at most two existing nets,
  permute re-route order, each route uses the Steiner approximation.  Explicitly
  not a global optimum over switch settings.
* ``true_exhaustive_feasible`` — on tiny single-chip topologies only
  (``x_lines * y_lines <= 6``, assignment size ``len(nets)+1 <= 3``), enumerate
  every subset of closed crosspoints and check multi-net connectivity, shorts,
  and exclusive-line ownership.  Returns ``None`` when the instance is too large.
"""

from __future__ import annotations

import copy
import random
from itertools import combinations, permutations, product
from typing import Any, Callable, Iterable, Sequence

from .crossbar import ResourceGraph, Vertex, build_resource_graph, find_shorts, small_topology
from .model import RouteError
from .rearrange import crossbar_create_net_with_rearrange
from .steiner import crossbar_create_net, crossbar_delete_net, crossbar_init_state

REFERENCE_MAX_NODES = 8
REFERENCE_MAX_NETS = 4
REFERENCE_MAX_RIPPED_NETS = 2
TRUE_EXHAUSTIVE_MAX_CROSSPOINTS = 6
TRUE_EXHAUSTIVE_MAX_ASSIGNMENT_NETS = 3
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


def _fixed_node_closure(seeds: Sequence[str], graph: ResourceGraph) -> set[str]:
    adjacency: dict[Vertex, list[Vertex]] = {vertex: [] for vertex in graph.vertices}
    for left, right in graph.fixed_edges:
        adjacency[left].append(right)
        adjacency[right].append(left)
    seen: set[Vertex] = set()
    stack: list[Vertex] = list(seeds)
    while stack:
        vertex = stack.pop()
        if vertex in seen:
            continue
        seen.add(vertex)
        stack.extend(adjacency[vertex])
    return {vertex for vertex in seen if isinstance(vertex, str)}


def _component_roots(
    graph: ResourceGraph, closed: Sequence[Sequence[object]]
) -> dict[Vertex, Vertex]:
    """Union-find roots under fixed edges plus ``closed`` crosspoints."""

    # Delegate crosspoint validation to find_shorts by probing with empty nets
    # first when closed is non-empty; for the roots walk, trust enumerated points.
    parent = {vertex: vertex for vertex in graph.vertices}

    def find(vertex: Vertex) -> Vertex:
        while parent[vertex] != vertex:
            parent[vertex] = parent[parent[vertex]]
            vertex = parent[vertex]
        return vertex

    closed_edges = []
    for value in closed:
        left, right = (value[0], "X", value[1]), (value[0], "Y", value[2])
        edge = (left, right) if left <= right else (right, left)
        closed_edges.append(edge)
    for left, right in (*graph.fixed_edges, *sorted(closed_edges, key=str)):
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left
    return {vertex: find(vertex) for vertex in graph.vertices}


def _config_feasible(
    graph: ResourceGraph,
    closed: Sequence[Sequence[object]],
    assignments: dict[str, list[str]],
) -> bool:
    # find_shorts validates crosspoints and reports multi-net electrical shorts;
    # component separation among assigned nodes also implies exclusive-line
    # ownership cannot overlap for those nets.
    if find_shorts(graph, closed, assignments):
        return False
    roots = _component_roots(graph, closed)
    for members in assignments.values():
        if not members:
            continue
        root = roots[members[0]]
        if any(roots[node] != root for node in members[1:]):
            return False
    return True


def true_exhaustive_feasible(
    state: dict[str, Any], net_id: str, nodes: Iterable[str]
) -> bool | None:
    """Return whether any switch config realizes the multi-net assignment.

    Returns ``None`` when the instance exceeds the tiny-topology bounds
    (single chip, ``x_lines * y_lines <= 6``, ``len(nets) + 1 <= 3``).  Does not
    rip existing nets: the target is existing nets unchanged plus the new net.
    """

    topology = state["topology"]
    chips = topology["chips"]
    if len(chips) != 1:
        return None
    chip = chips[0]
    crosspoint_count = int(chip["x_lines"]) * int(chip["y_lines"])
    if crosspoint_count > TRUE_EXHAUSTIVE_MAX_CROSSPOINTS:
        return None
    if len(state["nets"]) + 1 > TRUE_EXHAUSTIVE_MAX_ASSIGNMENT_NETS:
        return None

    supplied = list(nodes)
    if not all(isinstance(node, str) for node in supplied):
        return False
    members = sorted(set(supplied))
    attachments = topology["node_attachments"]
    if len(members) < 2:
        return False
    if any(node not in attachments for node in members):
        return False
    if not isinstance(net_id, str) or not net_id.strip() or net_id in state["nets"]:
        return False
    occupied = {node for net in state["nets"].values() for node in net["nodes"]}
    if occupied.intersection(members):
        return False

    graph = build_resource_graph(topology)
    fixed_nodes = _fixed_node_closure(members, graph)
    if fixed_nodes - set(members):
        return False

    assignments = {name: list(net["nodes"]) for name, net in state["nets"].items()}
    assignments[net_id] = members

    chip_name = chip["name"]
    crosspoints = sorted(
        (chip_name, x_line, y_line)
        for x_line in range(int(chip["x_lines"]))
        for y_line in range(int(chip["y_lines"]))
    )
    for mask in range(1 << len(crosspoints)):
        closed = [crosspoints[index] for index in range(len(crosspoints)) if mask & (1 << index)]
        if _config_feasible(graph, closed, assignments):
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
    true_compared = [
        record for record in records if record.get("true_reference") is not None
    ]
    true_agreements = sum(
        bool(record["accepted"]) == bool(record["true_reference"])
        for record in true_compared
    )
    divisor = attempts or 1
    comparison_divisor = len(compared) or 1
    true_divisor = len(true_compared) or 1
    return {
        "attempts": attempts,
        "accepted": successes,
        "rejected": attempts - successes,
        "success_rate": successes / divisor,
        "total_moves": sum(int(record["moves"]) for record in records),
        "avg_moves": sum(int(record["moves"]) for record in records) / divisor,
        "avg_states": sum(int(record["states"]) for record in records) / divisor,
        "timeouts": 0,
        "budget_misses": sum(bool(record["budget_miss"]) for record in records),
        "timeouts_or_budget_misses": sum(bool(record["budget_miss"]) for record in records),
        "true_budget_misses": sum(bool(record.get("budget_miss_true")) for record in records),
        "shorts_found": sum(int(record["shorts_found"]) for record in records),
        "line_exclusivity_violations": sum(
            int(record["line_exclusivity_violations"]) for record in records
        ),
        "compared_to_reference": {
            "cases": len(compared),
            "agreements": agreements,
            "disagreements": len(compared) - agreements,
            "agreement_rate": agreements / comparison_divisor,
        },
        "compared_to_true_exhaustive": {
            "cases": len(true_compared),
            "agreements": true_agreements,
            "disagreements": len(true_compared) - true_agreements,
            "agreement_rate": true_agreements / true_divisor,
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
        true_reference = true_exhaustive_feasible(state, net_id, nodes)
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
            "budget_miss_true": true_reference is True and not accepted,
            "line_exclusivity_violations": constraints["line_exclusivity_violations"],
            "moves": moves,
            "reference": reference,
            "shorts_found": constraints["shorts_found"],
            "states": states,
            "true_reference": true_reference,
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
    """Compare routers on identical seeded streams across 18 small topologies.

    Each router evolves its own state after accepting/rejecting a request;
    reference oracles are evaluated against that router's pre-create state.
    """

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
    for chips, x_lines, y_lines in product((1, 2, 3), (2, 3, 4), (2, 3)):
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
        "budgets": {
            "max_moves": max_moves,
            "max_states": max_states,
            "max_rip_nets": max_rip_nets,
        },
        "reference": {
            "description": (
                "two oracles: (1) neighborhood exhaustive within a separately "
                "bounded two-net rip-up/re-route search using Steiner per route; "
                "(2) true exhaustive over all crosspoint subsets on tiny "
                "single-chip instances only (x_lines*y_lines<=6, "
                "assignment nets<=3); neither claims global optimality outside "
                "those bounds"
            ),
            "max_existing_nets": reference_max_nets,
            "max_nodes": reference_max_nodes,
            "max_ripped_nets": REFERENCE_MAX_RIPPED_NETS,
            "true_exhaustive_max_assignment_nets": TRUE_EXHAUSTIVE_MAX_ASSIGNMENT_NETS,
            "true_exhaustive_max_crosspoints": TRUE_EXHAUSTIVE_MAX_CROSSPOINTS,
            "true_exhaustive_requires_single_chip": True,
        },
        "results": results,
        "seed": seed,
        "trials": trials,
    }
