from __future__ import annotations

import copy
import unittest
from unittest import mock

from jumperfree.routing.model import RouteError
from jumperfree.routing.scale_eval import (
    _plain_router,
    _replay,
    assert_scale_state,
    make_scale_workload,
    run_scale_evaluation,
)
from jumperfree.routing.steiner import crossbar_create_net, crossbar_init_state
from jumperfree.routing.v5_scale import v5_scale_topology


class ScaleEvaluationTests(unittest.TestCase):
    def test_fixed_workload_and_seeded_churn_are_deterministic(self) -> None:
        short = make_scale_workload(seed=7, trials=12)
        self.assertEqual(len(short), 12)
        self.assertEqual(short[0]["members"], ["BB01", "BB02"])
        self.assertEqual(short[2]["case"], "zero_state_budget_probe")
        self.assertEqual(short[3]["case"], "insufficient_move_budget_probe")
        self.assertEqual(short[3]["budgets"]["max_moves"], 6)
        first = make_scale_workload(seed=7, trials=60)
        second = make_scale_workload(seed=7, trials=60)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 60)
        self.assertIn("create", {item["action"] for item in first[12:]})
        self.assertIn("delete", {item["action"] for item in first[12:]})
        self.assertNotEqual(first, make_scale_workload(seed=8, trials=60))

    def test_congestion_recovery_and_budget_failures_are_atomic(self) -> None:
        state = crossbar_init_state(v5_scale_topology())
        state = crossbar_create_net(state, "old0", ["BB01", "BB02"])
        state = crossbar_create_net(state, "old1", ["BB03", "BB04"])
        snapshot = copy.deepcopy(state)
        with self.assertRaises(RouteError) as caught:
            crossbar_create_net(state, "new", ["BB05", "SF_L"])
        self.assertEqual(caught.exception.code, "NO_FREE_PATH")

        from jumperfree.routing.rearrange import crossbar_create_net_with_rearrange

        routed, stats = crossbar_create_net_with_rearrange(
            state, "new", ["BB05", "SF_L"],
            max_moves=64, max_states=32, max_rip_nets=3,
        )
        self.assertEqual(stats["moves"], 7)
        self.assertEqual(stats["states_explored"], 1)
        self.assertEqual(state, snapshot)
        self.assertEqual(assert_scale_state(routed)["shorts_found"], 0)
        for limits in ({"max_states": 0, "max_moves": 64},
                       {"max_states": 32, "max_moves": 6}):
            with self.subTest(limits=limits), self.assertRaises(RouteError):
                crossbar_create_net_with_rearrange(
                    state, "new", ["BB05", "SF_L"], max_rip_nets=3, **limits
                )
            self.assertEqual(state, snapshot)

    def test_short_replay_is_stable_safe_and_skips_oracles(self) -> None:
        first = run_scale_evaluation(seed=7, trials=18)
        second = run_scale_evaluation(seed=7, trials=18)
        self.assertEqual(first, second)
        self.assertEqual(first["topology"]["vertices"], 352)
        self.assertEqual(first["comparisons"], {
            "local_neighborhood_oracle": "unavailable_at_scale",
            "true_exhaustive_oracle": "unavailable_at_scale",
        })
        for result in first["results"]:
            self.assertEqual(result["constraints"], {
                "line_exclusivity_violations": 0,
                "shorts_found": 0,
                "undeclared_endpoint_joins": 0,
            })
            self.assertTrue(all(item["moves"] <= 64 for item in result["outcomes"]))
            self.assertTrue(all(item["states"] <= 32 for item in result["outcomes"]))
        plain, rearranged = first["results"]
        self.assertFalse(plain["outcomes"][4]["accepted"])
        self.assertTrue(rearranged["outcomes"][4]["accepted"])
        self.assertFalse(rearranged["outcomes"][2]["accepted"])
        self.assertFalse(rearranged["outcomes"][3]["accepted"])
        self.assertEqual(rearranged["outcomes"][4]["moves"], 7)

    def test_zero_and_exhausted_budgets_report_failures(self) -> None:
        for cap in (0, 1):
            with self.subTest(cap=cap):
                report = run_scale_evaluation(
                    seed=7,
                    trials=5,
                    max_moves=cap,
                    max_states=cap,
                    max_rip_nets=cap,
                )
                for router in report["results"]:
                    for outcome in router["outcomes"]:
                        self.assertLessEqual(outcome["moves"], cap)
                        self.assertLessEqual(outcome["states"], cap)
                        self.assertLessEqual(outcome["budgets"]["max_moves"], cap)
                        self.assertLessEqual(outcome["budgets"]["max_states"], cap)
                        self.assertLessEqual(outcome["budgets"]["max_rip_nets"], cap)
                probes = report["results"][1]["outcomes"][2:4]
                self.assertTrue(all(not probe["accepted"] for probe in probes))
                self.assertTrue(all(probe["states"] <= cap for probe in probes))
        result = report["results"][1]["outcomes"][4]
        self.assertFalse(result["accepted"])
        exhausted = run_scale_evaluation(
            seed=7, trials=5, max_moves=6, max_states=1, max_rip_nets=1
        )
        self.assertFalse(exhausted["results"][1]["outcomes"][4]["accepted"])

    def test_argument_validation(self) -> None:
        for kwargs in ({"seed": True}, {"seed": "7"}, {"trials": -1}, {"trials": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                make_scale_workload(**kwargs)  # type: ignore[arg-type]
        for name in ("max_moves", "max_states", "max_rip_nets"):
            for value in (-1, True, 1.5):
                with self.subTest(name=name, value=value), self.assertRaises(RouteError):
                    run_scale_evaluation(trials=0, **{name: value})  # type: ignore[arg-type]

    def test_invariant_checker_rejects_each_corruption(self) -> None:
        topology = v5_scale_topology()
        state = crossbar_create_net(
            crossbar_init_state(topology), "span", ["BB01", "SF_L"]
        )
        duplicate = copy.deepcopy(state)
        duplicate["nets"]["other"] = {
            "nodes": ["BB02", "BB03"],
            "lines": [state["nets"]["span"]["lines"][0]],
            "crosspoints": [],
        }
        with self.assertRaisesRegex(AssertionError, "owned by more than one"):
            assert_scale_state(duplicate)

        with mock.patch(
            "jumperfree.routing.scale_eval.find_shorts", return_value=[{"bad": True}]
        ), self.assertRaisesRegex(AssertionError, "short detected"):
            assert_scale_state(state)

        shared = crossbar_init_state(topology)
        shared = crossbar_create_net(shared, "a", ["BB01", "BB02"])
        shared = crossbar_create_net(shared, "b", ["BB09", "BB10"])
        shared["nets"]["a"]["crosspoints"].append(["A", 8, 1])
        with mock.patch(
            "jumperfree.routing.scale_eval.find_shorts", return_value=[]
        ), self.assertRaisesRegex(AssertionError, "share an electrical component"):
            assert_scale_state(shared)

        disconnected = copy.deepcopy(state)
        disconnected["nets"]["span"]["crosspoints"] = []
        with self.assertRaisesRegex(AssertionError, "not electrically connected"):
            assert_scale_state(disconnected)

        joined = copy.deepcopy(state)
        joined["topology"]["node_attachments"]["GHOST"] = ["A", "X", 8]
        with self.assertRaisesRegex(AssertionError, "undeclared endpoints"):
            assert_scale_state(joined)

        foreign = crossbar_create_net(
            crossbar_init_state(topology), "a", ["BB01", "BB02"]
        )
        foreign["nets"]["a"]["crosspoints"].append(["B", 10, 1])
        with self.assertRaisesRegex(AssertionError, "crosspoint outside"):
            assert_scale_state(foreign)

        for lines in ([], state["nets"]["span"]["lines"] + [["A", "X", 7]]):
            with self.subTest(lines=lines):
                corrupt = copy.deepcopy(state)
                corrupt["nets"]["span"]["lines"] = lines
                with self.assertRaisesRegex(AssertionError, "electrical component"):
                    assert_scale_state(corrupt)

    def test_replay_detects_mutation_budget_membership_and_failed_atomicity(self) -> None:
        topology = v5_scale_topology()
        operation = make_scale_workload(trials=1)

        def mutating(state, net_id, members, operation):
            updated = crossbar_create_net(state, net_id, members)
            state["leak"] = True
            return updated, {}

        with self.assertRaisesRegex(AssertionError, "mutated"):
            _replay(topology, operation, mutating, max_moves=64, max_states=32)

        def over_budget(state, net_id, members, operation):
            return crossbar_create_net(state, net_id, members), {"moves": 65}

        with self.assertRaisesRegex(AssertionError, "exceeded"):
            _replay(topology, operation, over_budget, max_moves=64, max_states=32)

        def changes_membership(state, net_id, members, operation):
            updated = crossbar_create_net(state, net_id, members)
            updated = crossbar_create_net(updated, "extra", ["BB03", "BB04"])
            return updated, {"moves": 0, "states_explored": 0}

        with self.assertRaisesRegex(AssertionError, "memberships"):
            _replay(topology, operation, changes_membership, max_moves=64, max_states=32)

        def failing_mutation(state, net_id, members, operation):
            state["leak"] = True
            raise RouteError("NO_FREE_PATH", "no")

        with self.assertRaisesRegex(AssertionError, "not atomic"):
            _replay(topology, operation, failing_mutation, max_moves=64, max_states=32)

        def failed_over_budget(state, net_id, members, operation):
            raise RouteError("NO_FREE_PATH", "no", states_explored=33)

        with self.assertRaisesRegex(AssertionError, "exceeded"):
            _replay(topology, operation, failed_over_budget, max_moves=64, max_states=32)

    def test_replay_recomputes_moves_and_propagates_internal_errors(self) -> None:
        topology = v5_scale_topology()
        workload = make_scale_workload(trials=5)
        operations = [workload[index] for index in (0, 1, 4)]

        def lying_router(state, net_id, members, operation):
            from jumperfree.routing.rearrange import crossbar_create_net_with_rearrange

            updated, stats = crossbar_create_net_with_rearrange(
                state, net_id, members, max_moves=64, max_states=32, max_rip_nets=3
            )
            stats["moves"] = 0
            return updated, stats

        with self.assertRaisesRegex(AssertionError, "independently computed C_move"):
            _replay(topology, operations, lying_router, max_moves=64, max_states=32)

        def internal_error(state, net_id, members, operation):
            raise RouteError("INTERNAL_SHORT", "injected")

        with self.assertRaisesRegex(RouteError, "injected"):
            _replay(topology, operations[:1], internal_error, max_moves=64, max_states=32)

        def invalid_return(state, net_id, members, operation):
            updated = crossbar_create_net(state, net_id, members)
            updated["nets"][net_id]["crosspoints"].append(["A", 99, 99])
            return updated, {"moves": 0, "states_explored": 0}

        with self.assertRaises(RouteError) as caught:
            _replay(topology, operations[:1], invalid_return, max_moves=64, max_states=32)
        self.assertEqual(caught.exception.code, "INVALID_CROSSPOINT")

        for extra_line in (False, True):
            with self.subTest(extra_line=extra_line):
                def corrupt_lines(state, net_id, members, operation):
                    updated = crossbar_create_net(state, net_id, members)
                    if extra_line:
                        updated["nets"][net_id]["lines"].append(["A", "X", 7])
                    else:
                        updated["nets"][net_id]["lines"] = []
                    return updated, {"moves": 0, "states_explored": 0}

                with self.assertRaisesRegex(AssertionError, "electrical component"):
                    _replay(
                        topology,
                        operations[:1],
                        corrupt_lines,
                        max_moves=64,
                        max_states=32,
                    )

    def test_crosspoint_owned_by_another_net_cannot_hide_endpoint_join(self) -> None:
        topology = v5_scale_topology()
        state = crossbar_init_state(topology)
        state = crossbar_create_net(state, "a", ["BB01", "BB02"])
        state = crossbar_create_net(state, "b", ["BB09", "BB10"])
        state["nets"]["a"]["crosspoints"].append(["B", 10, 1])
        with self.assertRaisesRegex(AssertionError, "undeclared endpoints"):
            assert_scale_state(state)

        operations = [
            {
                "action": "create",
                "case": "test",
                "members": ["BB01", "BB02"],
                "net": "a",
            },
            {
                "action": "create",
                "case": "test",
                "members": ["BB09", "BB10"],
                "net": "b",
            },
        ]

        def corrupt_owner(state, net_id, members, operation):
            updated = crossbar_create_net(state, net_id, members)
            if net_id == "b":
                updated["nets"]["a"]["crosspoints"].append(["B", 10, 1])
            return updated, {"moves": 0, "states_explored": 0}

        with self.assertRaisesRegex(AssertionError, "undeclared endpoints"):
            _replay(
                topology,
                operations,
                corrupt_owner,
                max_moves=64,
                max_states=32,
            )

    def test_plain_router_and_missing_delete_failure(self) -> None:
        topology = v5_scale_topology()
        state, stats = _plain_router(
            crossbar_init_state(topology), "a", ["BB01", "BB02"]
        )
        self.assertIn("a", state["nets"])
        self.assertEqual(stats, {"moves": 0, "states_explored": 0})
        result = _replay(
            topology,
            [{"action": "delete", "case": "test", "members": [], "net": "missing"}],
            _plain_router,
            max_moves=64,
            max_states=32,
        )
        self.assertEqual(result["failures"], 1)
        self.assertEqual(result["create_failures"], 0)
        self.assertEqual(result["delete_missing"], 1)
        self.assertEqual(result["outcomes"][0]["failure"]["code"], "NET_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
