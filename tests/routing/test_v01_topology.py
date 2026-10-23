from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from jumperfree.cli import main
from jumperfree.routing import v01_topology
from jumperfree.routing.crossbar import build_resource_graph, find_shorts, validate_topology
from jumperfree.routing.steiner import (
    crossbar_create_net,
    crossbar_delete_net,
    crossbar_init_state,
    crossbar_query,
)


class V01TopologyTests(unittest.TestCase):
    def test_exact_dimensions_and_complete_wiring(self) -> None:
        topology = v01_topology()
        validate_topology(topology)
        self.assertEqual(topology["chips"], [
            {"name": "C1", "x_lines": 16, "y_lines": 8},
            {"name": "C2", "x_lines": 16, "y_lines": 8},
        ])
        self.assertEqual(len(topology["node_attachments"]), 16)
        self.assertEqual(topology["node_attachments"], {
            **{f"Clip{line}": ["C1", "X", line] for line in range(8)},
            **{f"Clip{line + 8}": ["C2", "X", line] for line in range(8)},
        })
        self.assertEqual(len(topology["inter_chip_links"]), 8)
        self.assertEqual(topology["inter_chip_links"], [
            [["C1", "Y", line], ["C2", "Y", line]] for line in range(8)
        ])
        wired = {tuple(line) for line in topology["node_attachments"].values()}
        wired.update(tuple(line) for link in topology["inter_chip_links"] for line in link)
        unused = {(chip, "X", line) for chip in ("C1", "C2") for line in range(8, 16)}
        self.assertTrue(unused.isdisjoint(wired))

    def test_unverified_metadata_determinism_and_fresh_results(self) -> None:
        first = v01_topology()
        second = v01_topology()
        self.assertEqual(first, second)
        self.assertEqual(json.dumps(first), json.dumps(second))
        self.assertEqual(json.loads(json.dumps(first)), first)
        metadata = first["metadata"]
        self.assertEqual(metadata["model"], "jumperfree_v01_software_topology")
        self.assertIs(metadata["verified"], False)
        self.assertEqual({item["id"] for item in metadata["hardware_assumptions"]}, {
            "two_ch446q_style_chips", "clip_attachments",
            "unused_upper_x_lines", "eight_shared_y_channels",
        })
        self.assertTrue(all(item["verified"] is False
                            for item in metadata["hardware_assumptions"]))
        first["chips"][0]["x_lines"] = 1
        first["node_attachments"]["Clip0"][2] = 7
        first["inter_chip_links"][0][0][2] = 7
        metadata["hardware_assumptions"][0]["verified"] = True
        self.assertEqual(second, v01_topology())

    def test_resource_graph_counts_and_edges(self) -> None:
        topology = v01_topology()
        graph = build_resource_graph(topology)
        self.assertEqual(graph, build_resource_graph(v01_topology()))
        self.assertEqual(len(graph.vertices), 64)
        self.assertEqual(len(graph.exclusive_lines), 48)
        self.assertEqual(len(graph.crosspoint_edges), 256)
        self.assertEqual(len(graph.fixed_edges), 24)
        expected_fixed = {
            (tuple(line), node) for node, line in topology["node_attachments"].items()
        } | {(("C1", "Y", line), ("C2", "Y", line)) for line in range(8)}
        self.assertEqual(set(graph.fixed_edges), expected_fixed)
        self.assertEqual(set(graph.crosspoint_edges), {
            ((chip, "X", x), (chip, "Y", y))
            for chip in ("C1", "C2") for x in range(16) for y in range(8)
        })

    def test_cross_chip_route_uses_one_shared_y_channel(self) -> None:
        initial = crossbar_init_state(v01_topology())
        state = crossbar_create_net(initial, "span", ["Clip0", "Clip15"])
        self.assertEqual(state, crossbar_create_net(initial, "span", ["Clip15", "Clip0"]))
        self.assertEqual(initial["nets"], {})
        net = crossbar_query(state, "span")
        self.assertEqual(net["nodes"], ["Clip0", "Clip15"])
        self.assertEqual(net["crosspoints"], [["C1", 0, 0], ["C2", 7, 0]])
        self.assertEqual(net["lines"], [
            ["C1", "X", 0], ["C1", "Y", 0], ["C2", "X", 7], ["C2", "Y", 0],
        ])
        self.assertEqual(net["path_hops"], 2)
        self.assertEqual(crossbar_query(state)["occupancy"], {"free": 44, "total": 48, "used": 4})
        self.assertEqual(find_shorts(state["topology"], net["crosspoints"], {"span": net["nodes"]}), [])

    def test_same_chip_routes_reserve_y_lines_on_both_chips(self) -> None:
        state = crossbar_init_state(v01_topology())
        state = crossbar_create_net(state, "left", ["Clip0", "Clip1"])
        state = crossbar_create_net(state, "right", ["Clip8", "Clip9"])
        left, right = state["nets"]["left"], state["nets"]["right"]
        self.assertEqual(left["crosspoints"], [["C1", 0, 0], ["C1", 1, 0]])
        self.assertEqual(right["crosspoints"], [["C2", 0, 1], ["C2", 1, 1]])
        self.assertEqual(
            {tuple(line) for line in left["lines"] if line[1] == "Y"},
            {("C1", "Y", 0), ("C2", "Y", 0)},
        )
        self.assertEqual(
            {tuple(line) for line in right["lines"] if line[1] == "Y"},
            {("C1", "Y", 1), ("C2", "Y", 1)},
        )
        self.assertTrue({tuple(line) for line in left["lines"]}.isdisjoint(
            {tuple(line) for line in right["lines"]}
        ))
        self.assertEqual(find_shorts(
            state["topology"], left["crosspoints"] + right["crosspoints"],
            {"left": left["nodes"], "right": right["nodes"]},
        ), [])

    def test_all_eight_shared_channels_carry_independent_nets(self) -> None:
        topology = v01_topology()
        state = crossbar_init_state(topology)
        seen_lines: set[tuple[str, str, int]] = set()
        points = []
        assignments = {}
        for lane in range(8):
            name = f"pair{lane}"
            nodes = [f"Clip{lane}", f"Clip{lane + 8}"]
            state = crossbar_create_net(state, name, nodes)
            net = state["nets"][name]
            lines = {tuple(line) for line in net["lines"]}
            self.assertTrue(seen_lines.isdisjoint(lines))
            self.assertEqual(lines, {
                ("C1", "X", lane), ("C2", "X", lane),
                ("C1", "Y", lane), ("C2", "Y", lane),
            })
            seen_lines.update(lines)
            points.extend(net["crosspoints"])
            assignments[name] = nodes
        self.assertEqual(find_shorts(topology, points, assignments), [])
        self.assertEqual(crossbar_query(state)["occupancy"], {"free": 16, "total": 48, "used": 32})
        freed = crossbar_delete_net(state, "pair0")
        self.assertEqual(crossbar_create_net(freed, "pair0", ["Clip0", "Clip8"]), state)

    def invoke(self, arguments: list[str]) -> tuple[str, dict]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = main(arguments)
        self.assertEqual(status, 0)
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[1].startswith("ROUTE_JSON "))
        payload = json.loads(lines[1].removeprefix("ROUTE_JSON "))
        self.assertIs(payload["ok"], True)
        return lines[0], payload

    def test_cli_selects_v01_and_routes_persisted_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "v01.json"
            summary, payload = self.invoke([
                "route", "xbar", "init", "--topology", "v01",
                "--chips", "1", "--x-lines", "1", "--y-lines", "1",
                "--state", str(state_path),
            ])
            self.assertIn("lines=48", summary)
            self.assertEqual(payload["operation"], "init")
            self.assertEqual(payload["data"]["occupancy"], {"free": 48, "total": 48, "used": 0})
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved, crossbar_init_state(v01_topology()))
            self.invoke([
                "route", "xbar", "create", "--net", "span", "--nodes", "Clip0,Clip15",
                "--state", str(state_path),
            ])
            _, payload = self.invoke([
                "route", "xbar", "query", "--net", "span", "--state", str(state_path),
            ])
            self.assertEqual(payload["data"]["nodes"], ["Clip0", "Clip15"])
            self.assertEqual(payload["data"]["crosspoints"], [["C1", 0, 0], ["C2", 7, 0]])

    def test_cli_topology_file_overrides_v01(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            custom = {
                "chips": [{"name": "Q", "x_lines": 2, "y_lines": 1}],
                "node_attachments": {"a": ["Q", "X", 0], "b": ["Q", "X", 1]},
                "inter_chip_links": [],
            }
            topology_path = root / "topology.json"
            topology_path.write_text(json.dumps(custom), encoding="utf-8")
            state_path = root / "state.json"
            self.invoke([
                "route", "xbar", "init", "--topology", "v01",
                "--topology-file", str(topology_path), "--state", str(state_path),
            ])
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["topology"], custom)


if __name__ == "__main__":
    unittest.main()
