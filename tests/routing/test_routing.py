from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from jumperfree.cli import main
from jumperfree.routing.cli import run_route
from jumperfree.routing.model import (
    RouteError,
    create_net,
    delete_net,
    init_state,
    list_nets,
    query_state,
)

ROOT = Path(__file__).resolve().parents[2]


class RoutingModelTests(unittest.TestCase):
    def test_pairwise_connect_and_query(self) -> None:
        state = create_net(init_state(4), "pair", [3, 1])
        result = query_state(state, "pair")
        self.assertEqual(result["nodes"], [1, 3])
        self.assertEqual(result["edges"], [{"slot": 0, "u": 1, "v": 3}])
        self.assertTrue(result["connected"])

    def test_multiple_nets_are_connected_and_isolated(self) -> None:
        state = create_net(init_state(8, seed=11), "alpha", [0, 1, 2])
        state = create_net(state, "beta", [4, 5, 6])
        nets = list_nets(state)
        self.assertEqual([net["net"] for net in nets], ["alpha", "beta"])
        self.assertTrue(all(net["connected"] for net in nets))
        alpha_edges = {(edge["u"], edge["v"], edge["slot"]) for edge in nets[0]["edges"]}
        beta_edges = {(edge["u"], edge["v"], edge["slot"]) for edge in nets[1]["edges"]}
        self.assertTrue(alpha_edges.isdisjoint(beta_edges))
        self.assertTrue(set(nets[0]["nodes"]).isdisjoint(nets[1]["nodes"]))

    def test_remove_frees_nodes_and_resources_for_reconnect(self) -> None:
        initial = init_state(4, seed=2)
        first = create_net(initial, "old", [0, 1, 2])
        removed = delete_net(first, "old")
        second = create_net(removed, "new", [0, 1, 2])
        self.assertEqual(query_state(first, "old")["edges"], query_state(second, "new")["edges"])
        self.assertEqual(query_state(removed)["occupancy"]["used"], 0)

    def test_multinode_choice_is_deterministic_for_seed(self) -> None:
        left = create_net(init_state(6, seed=73, capacity=2), "n", [0, 1, 2, 3])
        right = create_net(init_state(6, seed=73, capacity=2), "n", [3, 2, 1, 0])
        self.assertEqual(left, right)
        self.assertEqual(len(query_state(left, "n")["edges"]), 3)

    def test_resource_exhaustion_reports_no_free_path(self) -> None:
        with self.assertRaises(RouteError) as caught:
            create_net(init_state(3, capacity=0), "blocked", [0, 1, 2])
        self.assertEqual(caught.exception.code, "NO_FREE_PATH")
        self.assertEqual(caught.exception.details["resource_reason"], "RESOURCE_EXHAUSTED")

    def test_create_rejects_invalid_requests(self) -> None:
        state = create_net(init_state(4), "first", [0, 1])
        cases = [
            ("", [2, 3], "INVALID_NET_ID"),
            ("first", [2, 3], "NET_ALREADY_EXISTS"),
            ("small", [2, 2], "NET_TOO_SMALL"),
            ("range", [2, 9], "NODE_OUT_OF_RANGE"),
            ("type", [2, "x"], "NODE_OUT_OF_RANGE"),
            ("overlap", [1, 2], "NODES_ALREADY_IN_OTHER_NET"),
        ]
        for net_id, nodes, code in cases:
            with self.subTest(code=code), self.assertRaises(RouteError) as caught:
                create_net(state, net_id, nodes)  # type: ignore[arg-type]
            self.assertEqual(caught.exception.code, code)

    def test_init_delete_and_query_validation_errors(self) -> None:
        for call, code in [
            (lambda: init_state(1), "INVALID_NODE_COUNT"),
            (lambda: init_state(2, capacity=-1), "INVALID_CAPACITY"),
            (lambda: delete_net(init_state(2), "missing"), "NET_NOT_FOUND"),
            (lambda: query_state(init_state(2), "missing"), "NET_NOT_FOUND"),
            (lambda: query_state({}), "INVALID_STATE"),
        ]:
            with self.subTest(code=code), self.assertRaises(RouteError) as caught:
                call()
            self.assertEqual(caught.exception.code, code)

    def test_query_reports_occupancy_and_disconnected_state(self) -> None:
        state = init_state(3, capacity=2)
        state["nets"]["manual"] = {"nodes": [0, 1, 2], "edges": [{"slot": 0, "u": 0, "v": 1}]}
        result = query_state(state)
        self.assertFalse(result["nets"][0]["connected"])
        self.assertEqual(result["occupancy"], {"free": 5, "total": 6, "used": 1})

    def test_route_error_dictionary_omits_empty_details(self) -> None:
        self.assertEqual(RouteError("CODE", "message").as_dict(), {"code": "CODE", "message": "message"})
        self.assertEqual(RouteError("CODE", "message", value=1).as_dict()["details"], {"value": 1})


class RoutingCliTests(unittest.TestCase):
    def invoke(self, arguments: list[str]) -> tuple[int, list[str], dict[str, object]]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(arguments)
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[1].startswith("ROUTE_JSON "))
        return code, lines, json.loads(lines[1].removeprefix("ROUTE_JSON "))

    def test_full_cli_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = str(Path(directory) / "nested" / "state.json")
            code, _, payload = self.invoke(["route", "init", "--nodes", "5", "--seed", "9", "--state", state])
            self.assertEqual(code, 0)
            self.assertTrue(payload["ok"])
            self.assertEqual(self.invoke(["route", "create", "--net", "z", "--nodes", "0,2,4", "--state", state])[0], 0)
            self.assertEqual(self.invoke(["route", "query", "--net", "z", "--state", state])[2]["data"]["nodes"], [0, 2, 4])  # type: ignore[index]
            self.assertEqual(len(self.invoke(["route", "list", "--state", state])[2]["data"]), 1)  # type: ignore[arg-type]
            self.assertEqual(self.invoke(["route", "delete", "--net", "z", "--state", state])[0], 0)
            self.assertEqual(self.invoke(["route", "list", "--state", state])[2]["data"], [])

    def test_cli_returns_structured_failures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = str(Path(directory) / "state.json")
            self.assertEqual(self.invoke(["route", "query", "--state", state])[2]["error"]["code"], "STATE_NOT_FOUND")  # type: ignore[index]
            self.invoke(["route", "init", "--nodes", "3", "--state", state])
            code, _, payload = self.invoke(["route", "create", "--net", "bad", "--nodes", "a,b", "--state", state])
            self.assertEqual(code, 1)
            self.assertEqual(payload["error"]["code"], "INVALID_NODE_LIST")  # type: ignore[index]
            Path(state).write_text("not-json", encoding="utf-8")
            self.assertEqual(self.invoke(["route", "list", "--state", state])[2]["error"]["code"], "INVALID_STATE")  # type: ignore[index]
            Path(state).write_text("[]", encoding="utf-8")
            self.assertEqual(self.invoke(["route", "list", "--state", state])[0], 1)

    def test_run_route_covers_invalid_loaded_state_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            state.write_text("{}", encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = run_route(Namespace(route_command="list", state=state))
            self.assertEqual(code, 1)
            self.assertIn("INVALID_STATE", output.getvalue())

    def test_python_module_cli_smoke_exit_codes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = str(Path(directory) / "state.json")
            commands = [
                (["route", "init", "--nodes", "4", "--state", state], 0),
                (["route", "create", "--net", "pair", "--nodes", "0,1", "--state", state], 0),
                (["route", "query", "--net", "pair", "--state", state], 0),
                (["route", "delete", "--net", "pair", "--state", state], 0),
                (["route", "query", "--net", "pair", "--state", state], 1),
            ]
            for arguments, expected in commands:
                with self.subTest(arguments=arguments):
                    completed = subprocess.run(
                        [sys.executable, "-m", "jumperfree", *arguments],
                        cwd=ROOT,
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(completed.returncode, expected, completed.stderr)
                    self.assertIn("ROUTE_JSON ", completed.stdout)


if __name__ == "__main__":
    unittest.main()
