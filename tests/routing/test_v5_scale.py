from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jumperfree.cli import main
from jumperfree.routing.crossbar import build_resource_graph
from jumperfree.routing.steiner import crossbar_create_net, crossbar_init_state
from jumperfree.routing.v5_scale import _reference, v5_scale_topology


class V5ScaleTopologyTests(unittest.TestCase):
    def test_exact_counts_mapping_metadata_and_determinism(self) -> None:
        topology = v5_scale_topology()
        graph = build_resource_graph(topology)
        self.assertEqual(topology, v5_scale_topology())
        self.assertEqual([chip["name"] for chip in topology["chips"]], list("ABCDEFGHIJKL"))
        self.assertTrue(all(chip["x_lines"] == 16 for chip in topology["chips"]))
        self.assertTrue(all(chip["y_lines"] == 8 for chip in topology["chips"]))
        self.assertEqual(len(topology["node_attachments"]), 64)
        self.assertEqual(len(graph.vertices), 352)
        self.assertEqual(len(graph.exclusive_lines), 288)
        self.assertEqual(len(graph.crosspoint_edges), 1536)
        self.assertEqual(len(graph.fixed_edges), 86)
        self.assertEqual(len(topology["inter_chip_links"]), 22)
        self.assertEqual(topology["inter_chip_links"][:2], [
            [["A", "Y", 0], ["B", "Y", 0]],
            [["A", "Y", 1], ["B", "Y", 1]],
        ])
        self.assertEqual(
            len({tuple(link[0]) for link in topology["inter_chip_links"][:2]}), 2
        )
        self.assertEqual(topology["node_attachments"]["BB01"], ["A", "X", 8])
        self.assertEqual(topology["node_attachments"]["BB60"], ["H", "X", 14])
        self.assertEqual(topology["node_attachments"]["SF_L"], ["L", "X", 15])
        assumptions = topology["metadata"]["hardware_assumptions"]
        self.assertEqual(len(assumptions), 3)
        self.assertTrue(all(item["verified"] is False for item in assumptions))
        self.assertIs(topology["metadata"]["reference_verified"], False)

    def test_twelve_chip_route_has_no_extra_endpoint(self) -> None:
        state = crossbar_create_net(
            crossbar_init_state(v5_scale_topology()), "span", ["BB01", "SF_L"]
        )
        net = state["nets"]["span"]
        self.assertEqual(net["nodes"], ["BB01", "SF_L"])
        self.assertEqual({line[0] for line in net["lines"]}, set("ABCDEFGHIJKL"))

    def test_cli_selects_scale_but_topology_file_still_wins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "scale.json"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main([
                    "route", "xbar", "init", "--topology", "v5-scale",
                    "--state", str(state_path),
                ])
            self.assertEqual(status, 0)
            self.assertIn("lines=288", output.getvalue())
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(len(saved["topology"]["chips"]), 12)

            custom = {
                "chips": [{"name": "Q", "x_lines": 2, "y_lines": 1}],
                "node_attachments": {"a": ["Q", "X", 0], "b": ["Q", "X", 1]},
                "inter_chip_links": [],
            }
            topology_path = Path(directory) / "topology.json"
            topology_path.write_text(json.dumps(custom), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                status = main([
                    "route", "xbar", "init", "--topology", "v5-scale",
                    "--topology-file", str(topology_path), "--state", str(state_path),
                ])
            self.assertEqual(status, 0)
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["topology"]["chips"][0]["name"], "Q")

    def test_packaged_reference_root_must_be_an_object(self) -> None:
        resource = mock.Mock()
        resource.joinpath.return_value.read_text.return_value = "[]"
        with mock.patch("jumperfree.routing.v5_scale.files", return_value=resource):
            with self.assertRaisesRegex(ValueError, "must contain an object"):
                _reference()


if __name__ == "__main__":
    unittest.main()
