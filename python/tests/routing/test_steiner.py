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
from jumperfree.routing.steiner import (
    crossbar_create_net,
    crossbar_delete_net,
    crossbar_init_state,
    crossbar_query,
)


class SteinerRoutingTests(unittest.TestCase):
    def test_multi_hop_path_crosses_inter_chip_links(self) -> None:
        topology = small_topology(chips=3, x_lines=2, y_lines=2)
        state = crossbar_create_net(crossbar_init_state(topology), "span", ["N5", "N2", "N0"])
        net = crossbar_query(state, "span")
        self.assertEqual(net["nodes"], ["N0", "N2", "N5"])
        self.assertEqual(net["path_hops"], 4)
        self.assertEqual({line[0] for line in net["lines"]}, {"C0", "C1", "C2"})
        self.assertEqual(find_shorts(topology, net["crosspoints"], {"span": net["nodes"]}), [])

    def test_multi_member_net_is_a_shared_tree(self) -> None:
        topology = small_topology(chips=1, x_lines=3, y_lines=1)
        state = crossbar_create_net(crossbar_init_state(topology), "tree", ["N2", "N0", "N1"])
        net = crossbar_query(state, "tree")
        self.assertEqual(net["path_hops"], 3)
        self.assertEqual(len(net["lines"]), 4)
        self.assertEqual(net["crosspoints"], [["C0", 0, 0], ["C0", 1, 0], ["C0", 2, 0]])

    def test_nets_are_isolated_and_have_no_shorts(self) -> None:
        topology = small_topology(chips=1, x_lines=4, y_lines=2)
        state = crossbar_create_net(crossbar_init_state(topology), "a", ["N0", "N1"])
        state = crossbar_create_net(state, "b", ["N2", "N3"])
        left = crossbar_query(state, "a")
        right = crossbar_query(state, "b")
        self.assertTrue({tuple(line) for line in left["lines"]}.isdisjoint(
            {tuple(line) for line in right["lines"]}
        ))
        points = left["crosspoints"] + right["crosspoints"]
        self.assertEqual(find_shorts(topology, points, {"a": ["N0", "N1"], "b": ["N2", "N3"]}), [])
        self.assertEqual(crossbar_query(state)["occupancy"], {"free": 0, "total": 6, "used": 6})

    def test_blocking_is_atomic_and_delete_frees_resources(self) -> None:
        initial = crossbar_init_state(small_topology(chips=1, x_lines=4, y_lines=1))
        occupied = crossbar_create_net(initial, "old", ["N0", "N1"])
        snapshot = copy.deepcopy(occupied)
        with self.assertRaises(RouteError) as caught:
            crossbar_create_net(occupied, "blocked", ["N2", "N3"])
        self.assertEqual(caught.exception.code, "NO_FREE_PATH")
        self.assertEqual(caught.exception.details["member"], "N3")
        self.assertEqual(caught.exception.details["resource_reason"], "RESOURCE_EXHAUSTED")
        self.assertIn("not proven impossible", caught.exception.message)
        self.assertEqual(occupied, snapshot)

        freed = crossbar_delete_net(occupied, "old")
        routed = crossbar_create_net(freed, "new", ["N2", "N3"])
        self.assertEqual(crossbar_query(freed)["occupancy"]["used"], 0)
        self.assertEqual(crossbar_query(routed, "new")["path_hops"], 2)

    def test_routing_is_deterministic(self) -> None:
        topology = small_topology(chips=2, x_lines=3, y_lines=2)
        first = crossbar_create_net(crossbar_init_state(topology), "n", ["N5", "N1", "N0"])
        second = crossbar_create_net(crossbar_init_state(topology), "n", ["N0", "N1", "N5"])
        self.assertEqual(first, second)
        encoded = json.dumps(first, sort_keys=True)
        self.assertEqual(encoded, json.dumps(second, sort_keys=True))

    def test_all_nodes_on_shared_attachment_line_are_required(self) -> None:
        topology = {
            "chips": [{"name": "A", "x_lines": 3, "y_lines": 2}],
            "node_attachments": {
                "a": ["A", "X", 0],
                "b": ["A", "X", 0],
                "z": ["A", "X", 0],
                "c": ["A", "X", 1],
                "d": ["A", "X", 2],
            },
            "inter_chip_links": [],
        }
        together = crossbar_create_net(crossbar_init_state(topology), "same", ["z", "b", "a"])
        self.assertEqual(crossbar_query(together, "same")["path_hops"], 0)

        initial = crossbar_init_state(topology)
        snapshot = copy.deepcopy(initial)
        with self.assertRaises(RouteError) as caught:
            crossbar_create_net(initial, "first", ["a", "c"])
        self.assertEqual(caught.exception.code, "NODES_PERMANENTLY_JOINED")
        self.assertEqual(caught.exception.details["missing"], ["b", "z"])
        self.assertEqual(initial, snapshot)

    def test_all_nodes_in_fixed_wire_component_are_required(self) -> None:
        topology = {
            "chips": [
                {"name": "A", "x_lines": 2, "y_lines": 1},
                {"name": "B", "x_lines": 1, "y_lines": 1},
            ],
            "node_attachments": {
                "a": ["A", "X", 0],
                "b": ["B", "X", 0],
                "c": ["A", "X", 1],
            },
            "inter_chip_links": [[["A", "X", 0], ["B", "X", 0]]],
        }
        initial = crossbar_init_state(topology)
        snapshot = copy.deepcopy(initial)
        with self.assertRaises(RouteError) as caught:
            crossbar_create_net(initial, "n", ["a", "c"])
        self.assertEqual(caught.exception.code, "NODES_PERMANENTLY_JOINED")
        self.assertEqual(caught.exception.details["missing"], ["b"])
        self.assertEqual(initial, snapshot)

    def test_routed_attachment_line_cannot_capture_an_unlisted_node(self) -> None:
        topology = {
            "chips": [{"name": "A", "x_lines": 2, "y_lines": 1}],
            "node_attachments": {
                "a": ["A", "X", 0],
                "b": ["A", "Y", 0],
                "c": ["A", "X", 1],
            },
            "inter_chip_links": [],
        }
        initial = crossbar_init_state(topology)
        snapshot = copy.deepcopy(initial)
        with self.assertRaises(RouteError) as caught:
            crossbar_create_net(initial, "n", ["a", "c"])
        self.assertEqual(caught.exception.code, "NODES_PERMANENTLY_JOINED")
        self.assertEqual(caught.exception.details["missing"], ["b"])
        self.assertEqual(initial, snapshot)

    def test_internal_short_check_is_not_optimization_dependent(self) -> None:
        initial = crossbar_init_state(small_topology(chips=1, x_lines=2, y_lines=1))
        reported = [{"nets": ["n", "other"], "nodes": ["N0", "N1"]}]
        with mock.patch("jumperfree.routing.steiner.find_shorts", return_value=reported):
            with self.assertRaises(RouteError) as caught:
                crossbar_create_net(initial, "n", ["N0", "N1"])
        self.assertEqual(caught.exception.code, "INTERNAL_SHORT")
        self.assertEqual(caught.exception.details["shorts"], reported)
        self.assertEqual(initial["nets"], {})

    def test_documented_error_codes(self) -> None:
        base = crossbar_init_state(small_topology(chips=1, x_lines=3, y_lines=2))
        routed = crossbar_create_net(base, "first", ["N0", "N1"])
        cases = [
            (lambda: crossbar_create_net(base, "x", ["N0", "missing"]), "NODE_NOT_IN_TOPOLOGY"),
            (lambda: crossbar_create_net(base, "x", ["N0", 2]), "NODE_NOT_IN_TOPOLOGY"),
            (lambda: crossbar_create_net(base, "x", ["N0", "N0"]), "NET_TOO_SMALL"),
            (lambda: crossbar_create_net(base, "", ["N0", "N1"]), "INVALID_NET_ID"),
            (lambda: crossbar_create_net(routed, "first", ["N1", "N2"]), "NET_ALREADY_EXISTS"),
            (lambda: crossbar_create_net(routed, "other", ["N1", "N2"]), "NODES_ALREADY_IN_OTHER_NET"),
            (lambda: crossbar_delete_net(base, "missing"), "NET_NOT_FOUND"),
            (lambda: crossbar_query(base, "missing"), "NET_NOT_FOUND"),
        ]
        for call, code in cases:
            with self.subTest(code=code), self.assertRaises(RouteError) as caught:
                call()
            self.assertEqual(caught.exception.code, code)

    def test_invalid_state_shapes_are_rejected(self) -> None:
        topology = small_topology(chips=1, x_lines=2, y_lines=1)
        valid = crossbar_create_net(crossbar_init_state(topology), "n", ["N0", "N1"])
        mutations: list[object] = [
            None,
            {},
            {**valid, "topology": {}},
            {**valid, "nets": {1: valid["nets"]["n"]}},
            {**valid, "nets": {"n": []}},
            {**valid, "nets": {"n": {"nodes": [], "lines": [], "crosspoints": []}}},
            {**valid, "nets": {"n": {**valid["nets"]["n"], "lines": [["C0", "Z", 0]]}}},
            {**valid, "nets": {"n": {**valid["nets"]["n"], "crosspoints": [["C0", True, 0]]}}},
        ]
        for state in mutations:
            with self.subTest(state=state), self.assertRaises(RouteError) as caught:
                crossbar_query(state)  # type: ignore[arg-type]
            self.assertEqual(caught.exception.code, "INVALID_STATE")


class CrossbarCliTests(unittest.TestCase):
    def invoke(self, arguments: list[str]) -> tuple[int, dict[str, object]]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(arguments)
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("ROUTE "))
        return code, json.loads(lines[1].removeprefix("ROUTE_JSON "))

    def test_cli_round_trip_with_small_and_file_topologies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state.json"
            self.assertEqual(self.invoke([
                "route", "xbar", "init", "--topology", "small", "--chips", "2",
                "--x-lines", "2", "--y-lines", "1", "--state", str(state),
            ])[0], 0)
            self.assertEqual(self.invoke([
                "route", "xbar", "create", "--net", "span", "--nodes", "N0,N3",
                "--state", str(state),
            ])[0], 0)
            data = self.invoke([
                "route", "xbar", "query", "--net", "span", "--state", str(state),
            ])[1]["data"]
            self.assertEqual(data["nodes"], ["N0", "N3"])  # type: ignore[index]
            self.assertEqual(self.invoke([
                "route", "xbar", "delete", "--net", "span", "--state", str(state),
            ])[0], 0)

            topology_file = root / "topology.json"
            topology_file.write_text(json.dumps(small_topology(chips=1)), encoding="utf-8")
            self.assertEqual(self.invoke([
                "route", "xbar", "init", "--topology-file", str(topology_file),
                "--state", str(state),
            ])[0], 0)

    def test_cli_reports_topology_and_state_failures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state.json"
            missing = root / "missing.json"
            code, payload = self.invoke([
                "route", "xbar", "init", "--topology-file", str(missing), "--state", str(state)
            ])
            self.assertEqual(code, 1)
            self.assertEqual(payload["error"]["code"], "INVALID_TOPOLOGY")  # type: ignore[index]
            bad = root / "bad.json"
            bad.write_text("not json", encoding="utf-8")
            self.assertEqual(self.invoke([
                "route", "xbar", "init", "--topology-file", str(bad), "--state", str(state)
            ])[0], 1)
            self.assertEqual(self.invoke([
                "route", "xbar", "query", "--state", str(state)
            ])[1]["error"]["code"], "STATE_NOT_FOUND")  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
