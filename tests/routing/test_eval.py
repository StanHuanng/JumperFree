from __future__ import annotations

import os
import unittest
from unittest import mock

from jumperfree.routing.crossbar import small_topology
from jumperfree.routing.eval import (
    _run_router,
    aggregate_metrics,
    check_constraints,
    exhaustive_reference,
    run_evaluation,
)
from jumperfree.routing.model import RouteError
from jumperfree.routing.steiner import crossbar_create_net, crossbar_init_state


class EvalTests(unittest.TestCase):
    def test_metric_aggregation_including_empty_input(self) -> None:
        records = [
            {"accepted": True, "moves": 2, "states": 3, "budget_miss": False,
             "shorts_found": 0, "line_exclusivity_violations": 0, "reference": True},
            {"accepted": False, "moves": 0, "states": 5, "budget_miss": True,
             "shorts_found": 1, "line_exclusivity_violations": 2, "reference": True},
            {"accepted": True, "moves": 1, "states": 1, "budget_miss": False,
             "shorts_found": 0, "line_exclusivity_violations": 0, "reference": None},
        ]
        metrics = aggregate_metrics(records)
        self.assertEqual(metrics["attempts"], 3)
        self.assertEqual(metrics["success_rate"], 2 / 3)
        self.assertEqual(metrics["avg_moves"], 1)
        self.assertEqual(metrics["avg_states"], 3)
        self.assertEqual(metrics["timeouts"], 0)
        self.assertEqual(metrics["budget_misses"], 1)
        self.assertEqual(metrics["timeouts_or_budget_misses"], 1)
        self.assertEqual(metrics["shorts_found"], 1)
        self.assertEqual(metrics["line_exclusivity_violations"], 2)
        self.assertEqual(metrics["compared_to_reference"], {"cases": 2, "agreement_rate": 0.5})
        self.assertEqual(aggregate_metrics([])["success_rate"], 0)
        self.assertEqual(aggregate_metrics([])["compared_to_reference"]["agreement_rate"], 0)

    def test_reference_checks_all_rip_sets_and_orderings(self) -> None:
        topology = small_topology(chips=2, x_lines=3, y_lines=2)
        state = crossbar_create_net(crossbar_init_state(topology), "old", ["N0", "N1"])
        self.assertTrue(exhaustive_reference(state, "new", ["N2", "N3"], max_ripped_nets=1))
        self.assertFalse(exhaustive_reference(state, "new", ["N2", "N3"], max_ripped_nets=0))

    def test_constraint_checker_counts_duplicate_lines_and_shorts(self) -> None:
        topology = small_topology(chips=1, x_lines=2, y_lines=1)
        state = crossbar_create_net(crossbar_init_state(topology), "a", ["N0", "N1"])
        self.assertEqual(check_constraints(state), {
            "line_exclusivity_violations": 0, "shorts_found": 0
        })
        broken = {**state, "nets": {**state["nets"], "b": {
            "nodes": [],
            "lines": [state["nets"]["a"]["lines"][0]],
            "crosspoints": [],
        }}}
        checked = check_constraints(broken)
        self.assertEqual(checked["line_exclusivity_violations"], 1)
        self.assertEqual(checked["shorts_found"], 0)
        with mock.patch("jumperfree.routing.eval.find_shorts", return_value=[{"short": True}]):
            self.assertEqual(check_constraints(state)["shorts_found"], 1)

    def test_router_runner_failure_delete_and_constraint_guard(self) -> None:
        topology = small_topology(chips=1, x_lines=2, y_lines=1)
        operations = [("create", "a", ["N0", "N1"]), ("delete", "a", []),
                      ("delete", "missing", []), ("create", "b", ["N0", "N1"])]
        calls = 0

        def router(state, net_id, nodes):
            nonlocal calls
            calls += 1
            if calls == 1:
                return crossbar_create_net(state, net_id, nodes), {"moves": 1, "states_explored": 2}
            raise RouteError("NO_FREE_PATH", "no", states_explored=7)

        records = _run_router(topology, operations, router,
                              reference_max_nodes=0, reference_max_nets=0)
        self.assertEqual([record["accepted"] for record in records], [True, False])
        self.assertEqual(records[1]["states"], 7)
        with mock.patch(
            "jumperfree.routing.eval.check_constraints",
            side_effect=[
                {"shorts_found": 0, "line_exclusivity_violations": 0},
                {"shorts_found": 1, "line_exclusivity_violations": 0},
            ],
        ):
            with self.assertRaises(AssertionError):
                _run_router(topology, operations[:2],
                            lambda state, net_id, nodes: (
                                crossbar_create_net(state, net_id, nodes), {}
                            ),
                            reference_max_nodes=0, reference_max_nets=0)

    def test_router_runner_rejects_and_reverts_invalid_create(self) -> None:
        topology = small_topology(chips=1, x_lines=2, y_lines=1)
        operations = [
            ("create", "a", ["N0", "N1"]),
            ("create", "b", ["N0", "N1"]),
        ]
        calls = 0

        def router(state, net_id, nodes):
            nonlocal calls
            calls += 1
            self.assertEqual(state["nets"], {})
            updated = crossbar_create_net(state, net_id, nodes)
            if calls == 1:
                updated["nets"]["duplicate"] = {
                    "nodes": [],
                    "lines": [updated["nets"][net_id]["lines"][0]],
                    "crosspoints": [],
                }
                state["nets"]["leaked"] = {}
            return updated, {"moves": calls, "states_explored": calls + 1}

        with mock.patch(
            "jumperfree.routing.eval.find_shorts",
            side_effect=lambda graph, points, assignments: (
                [{"short": True}] if "duplicate" in assignments else []
            ),
        ):
            records = _run_router(
                topology,
                operations,
                router,
                reference_max_nodes=8,
                reference_max_nets=4,
            )

        self.assertEqual([record["accepted"] for record in records], [False, True])
        self.assertEqual([record["shorts_found"] for record in records], [1, 0])
        self.assertEqual(
            [record["line_exclusivity_violations"] for record in records], [1, 0]
        )
        self.assertTrue(records[0]["budget_miss"])

    def test_router_runner_only_suppresses_no_free_path(self) -> None:
        topology = small_topology(chips=1, x_lines=2, y_lines=1)
        operations = [("create", "a", ["N0", "N1"])]

        for code in (
            "INTERNAL_SHORT",
            "INVALID_REARRANGE_BUDGET",
            "INVALID_STATE",
            "NET_ALREADY_EXISTS",
        ):
            with self.subTest(code=code), self.assertRaises(RouteError) as caught:
                _run_router(
                    topology,
                    operations,
                    lambda state, net_id, nodes, code=code: (_ for _ in ()).throw(
                        RouteError(code, "unexpected")
                    ),
                    reference_max_nodes=0,
                    reference_max_nets=0,
                )
            self.assertEqual(caught.exception.code, code)

    def test_reference_only_suppresses_no_free_path(self) -> None:
        state = crossbar_init_state(small_topology(chips=1, x_lines=2, y_lines=1))
        with mock.patch(
            "jumperfree.routing.eval.crossbar_create_net",
            side_effect=RouteError("INTERNAL_SHORT", "unexpected"),
        ):
            with self.assertRaises(RouteError) as caught:
                exhaustive_reference(state, "a", ["N0", "N1"])
        self.assertEqual(caught.exception.code, "INTERNAL_SHORT")

    def test_tiny_golden_run_is_deterministic_and_safe(self) -> None:
        first = run_evaluation(seed=17, trials=2, max_states=4)
        second = run_evaluation(seed=17, trials=2, max_states=4)
        self.assertEqual(first, second)
        self.assertEqual(set(first), {"reference", "results", "seed", "trials"})
        self.assertEqual(len(first["results"]), 24)
        for result in first["results"]:
            self.assertEqual(result["shorts_found"], 0)
            self.assertEqual(result["line_exclusivity_violations"], 0)
            self.assertIn("agreement_rate", result["compared_to_reference"])
            self.assertEqual(set(result["topology"]),
                             {"chips", "node_count", "x_lines", "y_lines"})
        with self.assertRaises(ValueError):
            run_evaluation(seed=0, trials=-1)
        for arguments in ({"max_moves": -1}, {"max_states": -1}, {"max_moves": True}):
            with self.subTest(arguments=arguments), self.assertRaises(RouteError) as caught:
                run_evaluation(seed=0, trials=0, **arguments)
            self.assertEqual(caught.exception.code, "INVALID_REARRANGE_BUDGET")


@unittest.skipUnless(os.environ.get("JF_EVAL_SLOW") == "1", "set JF_EVAL_SLOW=1")
class SlowEvalTests(unittest.TestCase):
    def test_larger_exhaustive_run_remains_safe(self) -> None:
        report = run_evaluation(seed=101, trials=25, max_states=100)
        self.assertTrue(all(row["shorts_found"] == 0 for row in report["results"]))


if __name__ == "__main__":
    unittest.main()
