from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from jumperfree.routing.crossbar import small_topology
from jumperfree.routing.eval import (
    _run_router,
    aggregate_metrics,
    check_constraints,
    exhaustive_reference,
    run_evaluation,
    true_exhaustive_feasible,
)
from jumperfree.routing.model import RouteError
from jumperfree.routing.steiner import crossbar_create_net, crossbar_init_state

GOLDEN_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "eval_golden_seed1_trials2.json"
)


class EvalTests(unittest.TestCase):
    def test_metric_aggregation_including_empty_input(self) -> None:
        records = [
            {
                "accepted": True,
                "moves": 2,
                "states": 3,
                "budget_miss": False,
                "budget_miss_true": False,
                "shorts_found": 0,
                "line_exclusivity_violations": 0,
                "reference": True,
                "true_reference": True,
            },
            {
                "accepted": False,
                "moves": 0,
                "states": 5,
                "budget_miss": True,
                "budget_miss_true": True,
                "shorts_found": 1,
                "line_exclusivity_violations": 2,
                "reference": True,
                "true_reference": True,
            },
            {
                "accepted": True,
                "moves": 1,
                "states": 1,
                "budget_miss": False,
                "budget_miss_true": False,
                "shorts_found": 0,
                "line_exclusivity_violations": 0,
                "reference": None,
                "true_reference": None,
            },
        ]
        metrics = aggregate_metrics(records)
        self.assertEqual(metrics["attempts"], 3)
        self.assertEqual(metrics["success_rate"], 2 / 3)
        self.assertEqual(metrics["avg_moves"], 1)
        self.assertEqual(metrics["avg_states"], 3)
        self.assertEqual(metrics["timeouts"], 0)
        self.assertEqual(metrics["budget_misses"], 1)
        self.assertEqual(metrics["timeouts_or_budget_misses"], 1)
        self.assertEqual(metrics["true_budget_misses"], 1)
        self.assertEqual(metrics["shorts_found"], 1)
        self.assertEqual(metrics["line_exclusivity_violations"], 2)
        self.assertEqual(metrics["compared_to_reference"], {"cases": 2, "agreement_rate": 0.5})
        self.assertEqual(
            metrics["compared_to_true_exhaustive"], {"cases": 2, "agreement_rate": 0.5}
        )
        empty = aggregate_metrics([])
        self.assertEqual(empty["success_rate"], 0)
        self.assertEqual(empty["compared_to_reference"]["agreement_rate"], 0)
        self.assertEqual(empty["compared_to_true_exhaustive"]["cases"], 0)
        self.assertEqual(empty["compared_to_true_exhaustive"]["agreement_rate"], 0)
        self.assertEqual(empty["true_budget_misses"], 0)

    def test_reference_checks_all_rip_sets_and_orderings(self) -> None:
        topology = small_topology(chips=2, x_lines=3, y_lines=2)
        state = crossbar_create_net(crossbar_init_state(topology), "old", ["N0", "N1"])
        self.assertTrue(exhaustive_reference(state, "new", ["N2", "N3"], max_ripped_nets=1))
        self.assertFalse(exhaustive_reference(state, "new", ["N2", "N3"], max_ripped_nets=0))

    def test_true_exhaustive_constructive_true_and_false(self) -> None:
        topology = small_topology(chips=1, x_lines=2, y_lines=2)
        empty = crossbar_init_state(topology)
        self.assertTrue(true_exhaustive_feasible(empty, "a", ["N0", "N1"]))
        self.assertTrue(exhaustive_reference(empty, "a", ["N0", "N1"], max_ripped_nets=0))
        placed = crossbar_create_net(empty, "a", ["N0", "N1"])
        self.assertFalse(true_exhaustive_feasible(placed, "b", ["N0", "N1"]))
        self.assertFalse(true_exhaustive_feasible(empty, "a", ["N0"]))
        self.assertFalse(true_exhaustive_feasible(empty, "a", ["N0", "missing"]))
        self.assertFalse(true_exhaustive_feasible(placed, "a", ["N0", "N1"]))
        self.assertFalse(true_exhaustive_feasible(empty, " ", ["N0", "N1"]))

    def test_true_exhaustive_permanently_joined_false(self) -> None:
        topology = {
            "chips": [{"name": "C0", "x_lines": 2, "y_lines": 2}],
            "node_attachments": {
                "N0": ["C0", "X", 0],
                "N1": ["C0", "X", 0],
                "N2": ["C0", "X", 1],
            },
            "inter_chip_links": [],
        }
        state = crossbar_init_state(topology)
        self.assertFalse(true_exhaustive_feasible(state, "a", ["N0", "N2"]))

    def test_true_exhaustive_no_feasible_switch_config(self) -> None:
        # Four nodes on a 2x2: connecting one X-pair always captures a Y-attached
        # node, so the two-net assignment is electrically impossible.
        topology = {
            "chips": [{"name": "C0", "x_lines": 2, "y_lines": 2}],
            "node_attachments": {
                "N0": ["C0", "X", 0],
                "N1": ["C0", "X", 1],
                "N2": ["C0", "Y", 0],
                "N3": ["C0", "Y", 1],
            },
            "inter_chip_links": [],
        }
        state = crossbar_init_state(topology)
        state["nets"] = {
            "a": {"nodes": ["N0", "N1"], "lines": [], "crosspoints": []},
        }
        self.assertFalse(true_exhaustive_feasible(state, "b", ["N2", "N3"]))

    def test_true_exhaustive_rejects_non_string_and_empty_net_members(self) -> None:
        topology = small_topology(chips=1, x_lines=2, y_lines=2)
        state = crossbar_init_state(topology)
        self.assertFalse(true_exhaustive_feasible(state, "a", ["N0", 1]))  # type: ignore[list-item]
        state["nets"]["ghost"] = {"nodes": [], "lines": [], "crosspoints": []}
        # Empty existing net is skipped in connectivity; N0-N1 still feasible.
        self.assertTrue(true_exhaustive_feasible(state, "a", ["N0", "N1"]))

    def test_true_exhaustive_oversized_returns_none(self) -> None:

        multi = crossbar_init_state(small_topology(chips=2, x_lines=2, y_lines=2))
        self.assertIsNone(true_exhaustive_feasible(multi, "a", ["N0", "N1"]))
        big = crossbar_init_state(small_topology(chips=1, x_lines=3, y_lines=3))
        self.assertIsNone(true_exhaustive_feasible(big, "a", ["N0", "N1"]))
        # assignment bound: three existing nets already → +1 exceeds 3
        custom = {
            "chips": [{"name": "C0", "x_lines": 2, "y_lines": 2}],
            "node_attachments": {
                "A0": ["C0", "X", 0],
                "A1": ["C0", "Y", 0],
                "B0": ["C0", "X", 1],
                "B1": ["C0", "Y", 1],
            },
            "inter_chip_links": [],
        }
        # Only two X and two Y lines → at most two disjoint pairs; fabricate three
        # nets in state without routing validity to trip the size bound.
        state = crossbar_init_state(custom)
        state["nets"] = {
            "n0": {"nodes": ["A0", "A1"], "lines": [], "crosspoints": []},
            "n1": {"nodes": ["B0", "B1"], "lines": [], "crosspoints": []},
            "n2": {"nodes": ["A0", "B0"], "lines": [], "crosspoints": []},
        }
        self.assertIsNone(true_exhaustive_feasible(state, "n3", ["A1", "B1"]))

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
        self.assertIsNotNone(records[0]["true_reference"])
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
        self.assertTrue(records[0]["budget_miss_true"])

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
        self.assertTrue(first["reference"]["true_exhaustive_requires_single_chip"])
        self.assertEqual(first["reference"]["true_exhaustive_max_crosspoints"], 6)
        for result in first["results"]:
            self.assertEqual(result["shorts_found"], 0)
            self.assertEqual(result["line_exclusivity_violations"], 0)
            self.assertIn("agreement_rate", result["compared_to_reference"])
            self.assertIn("agreement_rate", result["compared_to_true_exhaustive"])
            self.assertIn("true_budget_misses", result)
            self.assertEqual(set(result["topology"]),
                             {"chips", "node_count", "x_lines", "y_lines"})
        with self.assertRaises(ValueError):
            run_evaluation(seed=0, trials=-1)
        for arguments in ({"max_moves": -1}, {"max_states": -1}, {"max_moves": True}):
            with self.subTest(arguments=arguments), self.assertRaises(RouteError) as caught:
                run_evaluation(seed=0, trials=0, **arguments)
            self.assertEqual(caught.exception.code, "INVALID_REARRANGE_BUDGET")

    def test_golden_metrics_seed1_trials2_exact_match(self) -> None:
        report = run_evaluation(
            seed=1, trials=2, max_moves=8, max_states=2000, max_rip_nets=3
        )
        rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
        expected = GOLDEN_PATH.read_text(encoding="utf-8")
        self.assertEqual(rendered, expected)


@unittest.skipUnless(os.environ.get("JF_EVAL_SLOW") == "1", "set JF_EVAL_SLOW=1")
class SlowEvalTests(unittest.TestCase):
    def test_larger_exhaustive_run_remains_safe(self) -> None:
        report = run_evaluation(seed=101, trials=25, max_states=100)
        self.assertTrue(all(row["shorts_found"] == 0 for row in report["results"]))


if __name__ == "__main__":
    unittest.main()
