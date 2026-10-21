from __future__ import annotations

import json
import unittest
from pathlib import Path

from jumperfree.routing.crossbar import (
    ResourceGraph,
    build_resource_graph,
    find_shorts,
    small_topology,
    validate_topology,
)
from jumperfree.routing.model import RouteError

ROOT = Path(__file__).resolve().parents[2]


class CrossbarTests(unittest.TestCase):
    def test_small_topology_is_deterministic_and_graph_counts_match(self) -> None:
        topology = small_topology()
        self.assertEqual(topology, small_topology())
        self.assertEqual(len(small_topology(chips=2, y_lines=1)["inter_chip_links"]), 1)
        self.assertEqual(small_topology(chips=1, y_lines=1, links_per_pair=9)["inter_chip_links"], [])
        graph = build_resource_graph(topology)
        self.assertIsInstance(graph, ResourceGraph)
        self.assertEqual(len(graph.vertices), 24)
        self.assertEqual(len(graph.exclusive_lines), 16)
        self.assertEqual(len(graph.fixed_edges), 9)
        self.assertEqual(len(graph.crosspoint_edges), 32)
        self.assertEqual(graph.vertices, tuple(sorted(graph.vertices, key=str)))

    def test_validation_reports_bad_references_and_duplicates(self) -> None:
        cases = [
            {"chips": [], "node_attachments": {}, "inter_chip_links": []},
            {
                "chips": [{"name": "A", "x_lines": 1, "y_lines": 1}],
                "node_attachments": {"n": ["missing", "X", 0]},
                "inter_chip_links": [],
            },
            {
                "chips": [
                    {"name": "A", "x_lines": 1, "y_lines": 1},
                    {"name": "B", "x_lines": 1, "y_lines": 1},
                ],
                "node_attachments": {},
                "inter_chip_links": [
                    [["A", "Y", 0], ["B", "Y", 0]],
                    [["B", "Y", 0], ["A", "Y", 0]],
                ],
            },
        ]
        for topology in cases:
            with self.subTest(topology=topology), self.assertRaises(RouteError) as caught:
                validate_topology(topology)
            self.assertEqual(caught.exception.code, "INVALID_TOPOLOGY")

        shared_line = {
            "chips": [
                {"name": name, "x_lines": 1, "y_lines": 1} for name in ("A", "B", "C")
            ],
            "node_attachments": {},
            "inter_chip_links": [
                [["A", "Y", 0], ["B", "Y", 0]],
                [["B", "Y", 0], ["C", "Y", 0]],
            ],
        }
        validate_topology(shared_line)
        self.assertEqual(len(build_resource_graph(shared_line).fixed_edges), 2)

    def test_all_validation_paths_have_structured_errors(self) -> None:
        valid = small_topology(chips=1, x_lines=1, y_lines=1, links_per_pair=0)
        mutations = [
            None,
            {},
            {**valid, "chips": ["A"]},
            {**valid, "chips": [{"name": "", "x_lines": 1, "y_lines": 1}]},
            {**valid, "chips": [{"name": "A", "x_lines": True, "y_lines": 1}]},
            {**valid, "chips": [valid["chips"][0], valid["chips"][0]]},
            {**valid, "node_attachments": []},
            {**valid, "inter_chip_links": {}},
            {**valid, "node_attachments": {"": ["C0", "X", 0]}},
            {**valid, "node_attachments": {"a": []}},
            {**valid, "node_attachments": {"a": ["C0", "Z", 0]}},
            {**valid, "node_attachments": {"a": ["C0", "X", True]}},
            {**valid, "node_attachments": {"a": ["C0", "X", 9]}},
            {**valid, "inter_chip_links": [[]]},
            {**small_topology(), "inter_chip_links": [[['C0', 'Y', 0], ['C0', 'Y', 1]]]},
        ]
        for topology in mutations:
            with self.subTest(topology=topology), self.assertRaises(RouteError) as caught:
                validate_topology(topology)
            self.assertEqual(caught.exception.code, "INVALID_TOPOLOGY")
        for arguments in [("2", 1, 1, 0), (0, 1, 1, 0), (3, 1, 1, 1)]:
            with self.subTest(arguments=arguments), self.assertRaises(RouteError):
                small_topology(
                    arguments[0], arguments[1], arguments[2],
                    links_per_pair=arguments[3],
                )  # type: ignore[arg-type]
        with self.assertRaises(RouteError):
            small_topology(node_prefix="")

        shared_attachment = {
            **valid,
            "node_attachments": {"a": ["C0", "X", 0], "b": ["C0", "X", 0]},
        }
        validate_topology(shared_attachment)
        self.assertEqual(len(build_resource_graph(shared_attachment).fixed_edges), 2)

    def test_find_shorts_positive_negative_and_assignment_forms(self) -> None:
        topology = small_topology(chips=1, x_lines=2, y_lines=1, links_per_pair=0)
        closed = [("C0", 0, 0), ("C0", 1, 0)]
        self.assertEqual(find_shorts(topology, closed, {"N0": "same", "N1": "same"}), [])
        graph = build_resource_graph(topology)
        self.assertEqual(
            find_shorts(graph, closed, {"left": ["N0"], "right": ["N1"]}),
            [{"nets": ["left", "right"], "nodes": ["N0", "N1"]}],
        )
        self.assertEqual(find_shorts(graph, [("C0", 0, 0)], {"N0": "a", "N1": "b"}), [])

    def test_find_shorts_rejects_invalid_inputs(self) -> None:
        graph = build_resource_graph(small_topology(chips=1, x_lines=1, y_lines=1))
        cases = [
            lambda: find_shorts(graph, [("C0", 2, 0)], {"N0": "a"}),
            lambda: find_shorts(graph, [("C0", "x", 0)], {"N0": "a"}),
            lambda: find_shorts(graph, [], {"missing": "a"}),
            lambda: find_shorts(graph, [], {"a": "N0", "b": ["N0"]}),
            lambda: find_shorts(graph, [], {"a": ["N0"], "b": ["N0"]}),
        ]
        for call in cases:
            with self.subTest(call=call), self.assertRaises(RouteError):
                call()

    def test_reference_json_is_explicitly_unverified(self) -> None:
        path = ROOT / "jumperfree/routing/topologies/jumperless_v5_reference.json"
        reference = json.loads(path.read_text(encoding="utf-8"))
        self.assertIs(reference["verified"], False)
        self.assertEqual(reference["chip_count"], 12)
        self.assertEqual(len(reference["chips"]), 12)
        self.assertEqual(reference["breadboard_row_count"], 60)
        self.assertIn("physical hole is not a node", reference["counting_basis"].lower())


if __name__ == "__main__":
    unittest.main()
