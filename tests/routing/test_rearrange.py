from __future__ import annotations

import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jumperfree.cli import main
from jumperfree.routing.crossbar import find_shorts, small_topology
from jumperfree.routing.model import RouteError
from jumperfree.routing.rearrange import crossbar_create_net_with_rearrange
from jumperfree.routing.steiner import crossbar_create_net, crossbar_init_state


def blocked_state(*, with_side_net: bool = False) -> dict[str, object]:
    topology = small_topology(chips=2, x_lines=3, y_lines=2, links_per_pair=1)
    state = crossbar_create_net(crossbar_init_state(topology), "old", ["N0", "N1"])
    if with_side_net:
        state = crossbar_create_net(state, "side", ["N4", "N5"])
    return state


class RearrangeTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
