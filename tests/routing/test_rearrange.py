from __future__ import annotations

import contextlib
import copy
import heapq
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jumperfree.cli import main
from jumperfree.routing.crossbar import find_shorts, small_topology
from jumperfree.routing.eval import (
    _rearrange_router,
    _run_router,
    aggregate_metrics,
    check_constraints,
    exhaustive_reference,
)
from jumperfree.routing.model import RouteError
from jumperfree.routing.rearrange import (
    _rank_candidate_nets,
    crossbar_create_net_with_rearrange,
)
from jumperfree.routing.steiner import crossbar_create_net, crossbar_init_state

ORDER_CASES_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "rearrange_order_cases.json"
)
RETRY_CASE_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "rearrange_retry_cases.json"
)


def order_case_state(name: str) -> dict[str, object]:
    cases = json.loads(ORDER_CASES_PATH.read_text(encoding="utf-8"))
    case = cases[name]
    state = crossbar_init_state(case["topology"])
    for _operation, net_id, nodes in case["operations"][:2]:
        state = crossbar_create_net(state, net_id, nodes)
    return state


def blocked_state(*, with_side_net: bool = False) -> dict[str, object]:
    topology = small_topology(chips=2, x_lines=3, y_lines=2, links_per_pair=1)
    state = crossbar_create_net(crossbar_init_state(topology), "old", ["N0", "N1"])
    if with_side_net:
        state = crossbar_create_net(state, "side", ["N4", "N5"])
    return state


def redundant_fixed_link_topology() -> dict[str, object]:
    topology = small_topology(chips=2, x_lines=2, y_lines=3, links_per_pair=0)
    topology["inter_chip_links"] = [
        [["C0", "Y", 0], ["C1", "Y", 0]],
        [["C0", "Y", 2], ["C1", "Y", 2]],
        [["C0", "Y", 2], ["C1", "Y", 0]],
        [["C0", "Y", 0], ["C1", "Y", 2]],
    ]
    return topology


def three_link_blocked_state() -> dict[str, object]:
    topology = small_topology(chips=4, x_lines=3, y_lines=3, links_per_pair=1)
    state = crossbar_init_state(topology)
    for net_id, nodes in (
        ("a", ["N0", "N1"]),
        ("b", ["N3", "N4"]),
        ("c", ["N6", "N7"]),
    ):
        state = crossbar_create_net(state, net_id, nodes)
    return state


class RearrangeTests(unittest.TestCase):
    def test_later_reverse_retry_recovers_oracle_yes_case(self) -> None:
        case = json.loads(RETRY_CASE_PATH.read_text(encoding="utf-8"))
        state = crossbar_init_state(case["topology"])
        for _operation, net_id, nodes in case["operations"][:-1]:
            state = crossbar_create_net(state, net_id, nodes)
        _operation, net_id, nodes = case["operations"][-1]
        snapshot = copy.deepcopy(state)
        self.assertTrue(exhaustive_reference(state, net_id, nodes))
        self.assertEqual(_rank_candidate_nets(state, nodes), case["candidate_ids"])

        # The legacy fallback spent its only retry on (a, c). That pair can
        # recreate new and a, but neither ordering can recreate c. The next
        # promising pair (b, c) requires c before b to recover the request.
        for ordered in (("a", "c"), ("c", "a"), ("b", "c")):
            reduced = copy.deepcopy(state)
            for name in ordered:
                del reduced["nets"][name]
            working = crossbar_create_net(reduced, net_id, nodes)
            with self.subTest(order=ordered), self.assertRaises(RouteError):
                for name in ordered:
                    working = crossbar_create_net(working, name, state["nets"][name]["nodes"])

        first, stats = crossbar_create_net_with_rearrange(
            state, net_id, nodes, **case["budgets"]
        )
        # Reverse both request order and dictionary insertion order; neither
        # should affect candidate ranking, trials, or the routed result.
        reordered = {**state, "nets": dict(reversed(list(state["nets"].items())))}
        second, second_stats = crossbar_create_net_with_rearrange(
            reordered, net_id, reversed(nodes), **case["budgets"]
        )
        self.assertEqual(first, second)
        self.assertEqual(stats, second_stats)
        self.assertEqual(stats, case["expected_stats"])
        self.assertEqual(first["nets"]["a"], state["nets"]["a"])
        self.assertEqual(
            {name: net["nodes"] for name, net in first["nets"].items()},
            {**{name: net["nodes"] for name, net in state["nets"].items()}, net_id: nodes},
        )
        self.assertEqual(check_constraints(first), {
            "line_exclusivity_violations": 0, "shorts_found": 0,
        })
        self.assertEqual(state, snapshot)

        for limits, explored in (
            ({"max_states": case["legacy_states"]}, 7),
            ({"max_moves": 17}, 8),
            ({"max_rip_nets": 1}, 3),
        ):
            with self.subTest(limits=limits), self.assertRaises(RouteError) as caught:
                crossbar_create_net_with_rearrange(
                    state, net_id, nodes, **{**case["budgets"], **limits}
                )
            self.assertEqual(caught.exception.code, "NO_FREE_PATH")
            self.assertEqual(caught.exception.details["states_explored"], explored)
            self.assertEqual(state, snapshot)

    def test_later_reverse_retry_reduces_oracle_disagreement(self) -> None:
        case = json.loads(RETRY_CASE_PATH.read_text(encoding="utf-8"))
        for max_states, accepted, misses, moves, states in (
            (case["legacy_states"], 3, 1, 0, 7),
            (case["budgets"]["max_states"], 4, 0, 18, 8),
        ):
            with self.subTest(max_states=max_states):
                records = _run_router(
                    case["topology"], case["operations"],
                    _rearrange_router(**{**case["budgets"], "max_states": max_states}),
                    reference_max_nodes=8, reference_max_nets=4,
                )
                metrics = aggregate_metrics(records)
                self.assertEqual(metrics["accepted"], accepted)
                self.assertEqual(metrics["budget_misses"], misses)
                self.assertEqual(metrics["compared_to_reference"]["disagreements"], misses)
                self.assertEqual(metrics["total_moves"], moves)
                self.assertTrue(records[-1]["reference"])
                self.assertEqual(records[-1]["states"], states)
                self.assertEqual(metrics["shorts_found"], 0)
                self.assertEqual(metrics["line_exclusivity_violations"], 0)

    def test_reverse_retry_recovers_deterministically(self) -> None:
        state = order_case_state("recovery")
        snapshot = copy.deepcopy(state)

        first, stats = crossbar_create_net_with_rearrange(
            state,
            "new",
            ["N4", "N5"],
            max_moves=18,
            max_states=4,
            max_rip_nets=2,
        )
        second, second_stats = crossbar_create_net_with_rearrange(
            state,
            "new",
            ["N5", "N4"],
            max_moves=18,
            max_states=4,
            max_rip_nets=2,
        )

        self.assertEqual(first, second)
        self.assertEqual(stats, second_stats)
        self.assertEqual(stats, {
            "moves": 18,
            "rearrange_attempted": True,
            "rearranged_nets": ["a", "b"],
            "states_explored": 4,
        })
        self.assertEqual(state, snapshot)
        points = [point for net in first["nets"].values() for point in net["crosspoints"]]
        assignments = {name: net["nodes"] for name, net in first["nets"].items()}
        self.assertEqual(find_shorts(first["topology"], points, assignments), [])

    def test_reverse_retry_obeys_exact_budgets_and_is_atomic(self) -> None:
        state = order_case_state("recovery")
        snapshot = copy.deepcopy(state)

        for limits, explored in (
            ({"max_moves": 18, "max_states": 3}, 3),
            ({"max_moves": 17, "max_states": 4}, 4),
        ):
            with self.subTest(limits=limits), self.assertRaises(RouteError) as caught:
                crossbar_create_net_with_rearrange(
                    state,
                    "new",
                    ["N4", "N5"],
                    max_rip_nets=2,
                    **limits,
                )
            self.assertEqual(caught.exception.code, "NO_FREE_PATH")
            self.assertEqual(caught.exception.details["states_explored"], explored)
            self.assertEqual(state, snapshot)

    def test_first_pass_success_is_preserved(self) -> None:
        state = order_case_state("preserve")
        snapshot = copy.deepcopy(state)

        routed, stats = crossbar_create_net_with_rearrange(
            state,
            "new",
            ["N4", "N5"],
            max_moves=18,
            max_states=4,
            max_rip_nets=2,
        )

        self.assertIn("new", routed["nets"])
        self.assertEqual(stats, {
            "moves": 18,
            "rearrange_attempted": True,
            "rearranged_nets": ["a", "b"],
            "states_explored": 3,
        })
        self.assertEqual(state, snapshot)

    def test_reverse_retry_handles_candidate_and_unexpected_errors(self) -> None:
        for code in ("NO_FREE_PATH", "UNEXPECTED"):
            state = order_case_state("recovery")
            snapshot = copy.deepcopy(state)
            retry_error = RouteError(code, "retry")

            def route(working, net_id, nodes):
                if net_id == "b" and sorted(working["nets"]) == ["new"]:
                    raise retry_error
                return crossbar_create_net(working, net_id, nodes)

            with self.subTest(code=code), mock.patch(
                "jumperfree.routing.rearrange.crossbar_create_net", side_effect=route
            ):
                with self.assertRaises(RouteError) as caught:
                    crossbar_create_net_with_rearrange(
                        state,
                        "new",
                        ["N4", "N5"],
                        max_moves=18,
                        max_states=4,
                        max_rip_nets=2,
                    )
            if code == "NO_FREE_PATH":
                self.assertEqual(caught.exception.code, "NO_FREE_PATH")
                self.assertEqual(caught.exception.details["states_explored"], 4)
            else:
                self.assertIs(caught.exception, retry_error)
            self.assertEqual(state, snapshot)

    def test_three_link_blockers_require_k3(self) -> None:
        state = three_link_blocked_state()
        snapshot = copy.deepcopy(state)
        requested = ["N2", "N5", "N8", "N9"]

        with self.assertRaises(RouteError):
            crossbar_create_net(state, "new", requested)
        with self.assertRaises(RouteError) as caught:
            crossbar_create_net_with_rearrange(
                state,
                "new",
                requested,
                max_moves=21,
                max_states=7,
                max_rip_nets=2,
            )
        self.assertEqual(caught.exception.details["resource_reason"], "RESOURCE_EXHAUSTED")
        self.assertEqual(caught.exception.details["states_explored"], 6)

        routed, stats = crossbar_create_net_with_rearrange(
            state, "new", requested, max_moves=21, max_states=7
        )
        self.assertEqual(stats, {
            "moves": 21,
            "rearrange_attempted": True,
            "rearranged_nets": ["a", "b", "c"],
            "states_explored": 7,
        })
        self.assertEqual(state, snapshot)
        self.assertEqual(
            {net_id: net["nodes"] for net_id, net in routed["nets"].items()},
            {
                "new": requested,
                "a": ["N0", "N1"],
                "b": ["N3", "N4"],
                "c": ["N6", "N7"],
            },
        )
        points = [point for net in routed["nets"].values() for point in net["crosspoints"]]
        assignments = {name: net["nodes"] for name, net in routed["nets"].items()}
        self.assertEqual(find_shorts(routed["topology"], points, assignments), [])

    def test_blocker_rank_beats_lexicographic_order(self) -> None:
        topology = small_topology(chips=2, x_lines=3, y_lines=2, links_per_pair=1)
        state = crossbar_create_net(
            crossbar_init_state(topology), "z_blocker", ["N0", "N1"]
        )
        state = crossbar_create_net(state, "a_side", ["N4", "N5"])

        naive_first = copy.deepcopy(state)
        del naive_first["nets"]["a_side"]
        with self.assertRaises(RouteError):
            crossbar_create_net(naive_first, "new", ["N2", "N3"])

        routed, stats = crossbar_create_net_with_rearrange(
            state, "new", ["N2", "N3"], max_moves=7, max_states=1
        )
        self.assertIn("new", routed["nets"])
        self.assertEqual(stats, {
            "moves": 7,
            "rearrange_attempted": True,
            "rearranged_nets": ["z_blocker"],
            "states_explored": 1,
        })

    def test_probe_keeps_zero_score_candidates(self) -> None:
        topology = small_topology(chips=2, x_lines=3, y_lines=2, links_per_pair=1)
        state = crossbar_create_net(
            crossbar_init_state(topology), "z_blocker", ["N0", "N1"]
        )
        state = crossbar_create_net(state, "a_side", ["N4", "N5"])
        snapshot = copy.deepcopy(state)

        # z_blocker's Y0 line and its fixed partner occupy the relaxed path.
        # a_side is off that path, but the advisory probe must not discard it.
        self.assertEqual(
            _rank_candidate_nets(state, ["N3", "N2"]),
            ["z_blocker", "a_side"],
        )
        self.assertEqual(state, snapshot)

    def test_probe_union_handles_redundant_and_out_of_order_fixed_links(self) -> None:
        state = crossbar_init_state(redundant_fixed_link_topology())

        # Parallel links first share a component, then require a root swap and
        # finally repeat an already-united pair.
        self.assertEqual(_rank_candidate_nets(state, ["N0", "N2"]), [])

    def test_probe_discards_stale_dijkstra_entries(self) -> None:
        state = crossbar_init_state(
            small_topology(chips=2, x_lines=2, y_lines=1)
        )
        real_pop = heapq.heappop
        stale_entry: tuple[int, int, tuple[str, str, str, int]] | None = None
        popped_stale: list[tuple[int, int, tuple[str, str, str, int]]] = []

        def pop_with_stale(queue):
            nonlocal stale_entry
            if stale_entry is None and len(queue) > 1:
                occupied, hops, current = real_pop(queue)
                stale_entry = (occupied + 1, hops + 1, current)
                heapq.heappush(queue, stale_entry)
                return occupied, hops, current
            entry = real_pop(queue)
            if entry == stale_entry:
                popped_stale.append(entry)
            return entry

        with mock.patch(
            "jumperfree.routing.rearrange.heapq.heappop", side_effect=pop_with_stale
        ):
            self.assertEqual(_rank_candidate_nets(state, ["N0", "N2"]), [])
        self.assertEqual(len(popped_stale), 1)

    def test_rip_limit_validation_and_budget_boundaries(self) -> None:
        blocked = three_link_blocked_state()
        snapshot = copy.deepcopy(blocked)
        requested = ["N2", "N5", "N8", "N9"]

        for value in (-1, True, 1.5, "3", None):
            with self.subTest(value=value), self.assertRaises(RouteError) as caught:
                crossbar_create_net_with_rearrange(
                    blocked, "new", requested, max_rip_nets=value  # type: ignore[arg-type]
                )
            self.assertEqual(caught.exception.code, "INVALID_REARRANGE_BUDGET")

        with mock.patch(
            "jumperfree.routing.rearrange._rank_candidate_nets"
        ) as rank, mock.patch(
            "jumperfree.routing.rearrange.crossbar_create_net", wraps=crossbar_create_net
        ) as route:
            with self.assertRaises(RouteError) as caught:
                crossbar_create_net_with_rearrange(
                    blocked, "new", requested, max_rip_nets=0
                )
        rank.assert_not_called()
        self.assertEqual(route.call_count, 1)
        self.assertEqual(caught.exception.details["states_explored"], 0)

        for limits, explored in (
            ({"max_states": 6, "max_moves": 21}, 6),
            ({"max_states": 7, "max_moves": 20}, 7),
        ):
            with self.subTest(limits=limits), self.assertRaises(RouteError) as caught:
                crossbar_create_net_with_rearrange(
                    blocked, "new", requested, **limits
                )
            self.assertEqual(caught.exception.details["states_explored"], explored)
            self.assertEqual(blocked, snapshot)

        roomy, stats = crossbar_create_net_with_rearrange(
            blocked,
            "new",
            requested,
            max_moves=21,
            max_states=7,
            max_rip_nets=99,
        )
        self.assertIn("new", roomy["nets"])
        self.assertEqual(stats["states_explored"], 7)

        ordinary, stats = crossbar_create_net_with_rearrange(
            crossbar_init_state(small_topology(chips=1, x_lines=2, y_lines=1)),
            "new",
            ["N0", "N1"],
            max_rip_nets=0,
        )
        self.assertIn("new", ordinary["nets"])
        self.assertIs(stats["rearrange_attempted"], False)

    def test_plain_failure_is_rearranged_once_with_exact_move_count(self) -> None:
        state = blocked_state(with_side_net=True)
        snapshot = copy.deepcopy(state)
        with self.assertRaises(RouteError) as caught:
            crossbar_create_net(state, "new", ["N2", "N3"])
        self.assertEqual(caught.exception.code, "NO_FREE_PATH")

        first, stats = crossbar_create_net_with_rearrange(
            state, "new", ["N3", "N2"], max_moves=7
        )
        second, second_stats = crossbar_create_net_with_rearrange(
            state, "new", ["N2", "N3"], max_moves=7
        )
        self.assertEqual(first, second)
        self.assertEqual(stats, second_stats)
        self.assertEqual(stats, {
            "moves": 7,
            "rearrange_attempted": True,
            "rearranged_nets": ["old"],
            "states_explored": 1,
        })
        self.assertEqual(state, snapshot)
        points = [point for net in first["nets"].values() for point in net["crosspoints"]]
        assignments = {name: net["nodes"] for name, net in first["nets"].items()}
        self.assertEqual(find_shorts(first["topology"], points, assignments), [])

    def test_normal_create_reports_zero_moves(self) -> None:
        state, stats = crossbar_create_net_with_rearrange(
            crossbar_init_state(small_topology(chips=1, x_lines=2, y_lines=1)),
            "new", ["N0", "N1"],
        )
        self.assertIn("new", state["nets"])
        self.assertEqual(stats, {
            "moves": 0,
            "rearrange_attempted": False,
            "rearranged_nets": [],
            "states_explored": 0,
        })

    def test_move_and_state_budgets_fail_atomically(self) -> None:
        state = blocked_state()
        snapshot = copy.deepcopy(state)
        for limits, explored in [({"max_moves": 6}, 1), ({"max_states": 0}, 0)]:
            with self.subTest(limits=limits), self.assertRaises(RouteError) as caught:
                crossbar_create_net_with_rearrange(state, "new", ["N2", "N3"], **limits)
            error = caught.exception
            self.assertEqual(error.code, "NO_FREE_PATH")
            self.assertEqual(error.message, (
                "no path in current free resources within rearrange budget; not proven impossible"
            ))
            self.assertEqual(error.details["resource_reason"], "RESOURCE_EXHAUSTED")
            self.assertIs(error.details["rearrange_attempted"], True)
            self.assertEqual(error.details["states_explored"], explored)
            self.assertEqual(state, snapshot)

        with mock.patch(
            "jumperfree.routing.rearrange.crossbar_create_net", wraps=crossbar_create_net
        ) as route:
            with self.assertRaises(RouteError) as caught:
                crossbar_create_net_with_rearrange(
                    state, "new", ["N2", "N3"], max_states=0
                )
        self.assertEqual(route.call_count, 1)
        self.assertEqual(caught.exception.details["states_explored"], 0)

        no_path = RouteError("NO_FREE_PATH", "blocked")
        with mock.patch(
            "jumperfree.routing.rearrange.crossbar_create_net",
            side_effect=[no_path, no_path],
        ) as route:
            with self.assertRaises(RouteError) as caught:
                crossbar_create_net_with_rearrange(
                    blocked_state(with_side_net=True),
                    "new",
                    ["N2", "N3"],
                    max_states=1,
                )
        self.assertEqual(route.call_count, 2)
        self.assertEqual(caught.exception.details["states_explored"], 1)

        impossible = small_topology(chips=1, x_lines=4, y_lines=1)
        full = crossbar_create_net(crossbar_init_state(impossible), "old", ["N0", "N1"])
        with self.assertRaises(RouteError) as caught:
            crossbar_create_net_with_rearrange(full, "new", ["N2", "N3"])
        self.assertEqual(caught.exception.details["resource_reason"], "RESOURCE_EXHAUSTED")

    def test_disconnected_topology_is_proven_unroutable(self) -> None:
        topology = small_topology(chips=2, x_lines=1, y_lines=1, links_per_pair=0)
        state = crossbar_init_state(topology)
        with self.assertRaises(RouteError) as caught:
            crossbar_create_net_with_rearrange(state, "span", ["N0", "N1"])
        self.assertEqual(caught.exception.code, "NO_FREE_PATH")
        self.assertEqual(caught.exception.details["resource_reason"], "UNROUTABLE_INVARIANT")
        self.assertEqual(caught.exception.details["states_explored"], 0)

    def test_invalid_inputs_and_internal_short_are_not_hidden(self) -> None:
        state = blocked_state()
        for value in (-1, True):
            with self.subTest(value=value), self.assertRaises(RouteError) as caught:
                crossbar_create_net_with_rearrange(state, "new", ["N2", "N3"], max_moves=value)
            self.assertEqual(caught.exception.code, "INVALID_REARRANGE_BUDGET")
        with self.assertRaises(RouteError) as caught:
            crossbar_create_net_with_rearrange(state, "old", ["N2", "N3"])
        self.assertEqual(caught.exception.code, "NET_ALREADY_EXISTS")

        reported = [{"nets": ["new", "old"], "nodes": ["N0", "N2"]}]
        with mock.patch("jumperfree.routing.rearrange.find_shorts", return_value=reported):
            with self.assertRaises(RouteError) as caught:
                crossbar_create_net_with_rearrange(state, "new", ["N2", "N3"])
        self.assertEqual(caught.exception.code, "INTERNAL_SHORT")
        self.assertEqual(caught.exception.details["shorts"], reported)

    def test_candidate_routing_failures_continue_search(self) -> None:
        topology = small_topology(chips=3, x_lines=2, y_lines=2)
        state = crossbar_create_net(
            crossbar_init_state(topology), "middle", ["N2", "N3"]
        )
        with self.assertRaises(RouteError) as caught:
            crossbar_create_net_with_rearrange(state, "new", ["N0", "N4"])
        self.assertEqual(caught.exception.code, "NO_FREE_PATH")
        self.assertEqual(caught.exception.details["resource_reason"], "RESOURCE_EXHAUSTED")
        self.assertEqual(caught.exception.details["states_explored"], 1)

        blocked = blocked_state()
        no_path = RouteError("NO_FREE_PATH", "blocked")
        reduced = copy.deepcopy(blocked)
        del reduced["nets"]["old"]
        probe = crossbar_create_net(reduced, "new", ["N2", "N3"])
        candidate_codes = [
            "NODES_PERMANENTLY_JOINED",
            "NODES_ALREADY_IN_OTHER_NET",
            "NO_FREE_PATH",
            "NET_ALREADY_EXISTS",
        ]
        for code in candidate_codes:
            for calls in ([no_path, RouteError(code, "candidate")],
                          [no_path, probe, RouteError(code, "candidate")]):
                with self.subTest(code=code, calls=len(calls)), mock.patch(
                    "jumperfree.routing.rearrange.crossbar_create_net", side_effect=calls
                ):
                    with self.assertRaises(RouteError) as caught:
                        crossbar_create_net_with_rearrange(
                            blocked, "new", ["N2", "N3"], max_states=1
                        )
                self.assertEqual(caught.exception.code, "NO_FREE_PATH")

    def test_unexpected_probe_and_trial_errors_propagate(self) -> None:
        state = blocked_state()
        no_path = RouteError("NO_FREE_PATH", "blocked")
        reduced = copy.deepcopy(state)
        del reduced["nets"]["old"]
        probe = crossbar_create_net(reduced, "new", ["N2", "N3"])
        for code in ("INTERNAL_SHORT", "INVALID_STATE", "UNEXPECTED"):
            for calls in ([no_path, RouteError(code, "bad")],
                          [no_path, probe, RouteError(code, "bad")]):
                error = calls[-1]
                with self.subTest(code=code, calls=len(calls)), mock.patch(
                    "jumperfree.routing.rearrange.crossbar_create_net", side_effect=calls
                ):
                    with self.assertRaises(RouteError) as caught:
                        crossbar_create_net_with_rearrange(state, "new", ["N2", "N3"])
                self.assertIs(caught.exception, error)


class RearrangeCliTests(unittest.TestCase):
    def invoke(self, arguments: list[str]) -> tuple[int, dict[str, object]]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(arguments)
        lines = output.getvalue().splitlines()
        return code, json.loads(lines[1].removeprefix("ROUTE_JSON "))

    def test_cli_flag_routes_and_includes_stats(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(json.dumps(blocked_state()), encoding="utf-8")
            code, payload = self.invoke([
                "route", "xbar", "create", "--net", "new", "--nodes", "N2,N3",
                "--rearrange", "--max-moves", "7", "--max-states", "1",
                "--state", str(path),
            ])
            self.assertEqual(code, 0)
            self.assertEqual(payload["rearrange"]["moves"], 7)  # type: ignore[index]

    def test_cli_forwards_max_rip_nets(self) -> None:
        for arguments, expected in (([], 3), (["--max-rip-nets", "1"], 1)):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "state.json"
                path.write_text(json.dumps(blocked_state()), encoding="utf-8")
                with mock.patch(
                    "jumperfree.routing.cli.crossbar_create_net_with_rearrange",
                    wraps=crossbar_create_net_with_rearrange,
                ) as rearrange:
                    code, _ = self.invoke([
                        "route", "xbar", "create", "--net", "new",
                        "--nodes", "N2,N3", "--rearrange", "--max-moves", "7",
                        "--state", str(path), *arguments,
                    ])
                self.assertEqual(code, 0)
                self.assertEqual(rearrange.call_args.kwargs["max_rip_nets"], expected)


if __name__ == "__main__":
    unittest.main()
