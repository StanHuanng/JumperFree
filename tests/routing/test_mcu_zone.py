from __future__ import annotations

import json
import unittest

from jumperfree.routing.crossbar import build_resource_graph, small_topology
from jumperfree.routing.model import RouteError


class McuZoneTopologyTests(unittest.TestCase):
    def test_disabled_zone_is_literal_legacy_snapshot(self) -> None:
        expected = {
            "chips": [
                {"name": "C0", "x_lines": 2, "y_lines": 3},
                {"name": "C1", "x_lines": 2, "y_lines": 3},
            ],
            "node_attachments": {
                "ROW0": ["C0", "X", 0],
                "ROW1": ["C0", "X", 1],
                "ROW2": ["C1", "X", 0],
                "ROW3": ["C1", "X", 1],
            },
            "inter_chip_links": [
                [["C0", "Y", 0], ["C1", "Y", 0]],
                [["C0", "Y", 1], ["C1", "Y", 1]],
            ],
        }
        self.assertEqual(
            small_topology(2, 2, 3, links_per_pair=2, node_prefix="ROW"), expected
        )
        self.assertEqual(
            small_topology(2, 2, 3, links_per_pair=2, node_prefix="ROW",
                           mcu_zone=False, clip_rows=99, clips_per_row=-1, mcu_links=0),
            expected,
        )

    def test_enabled_zone_has_distinct_deterministic_clip_bank(self) -> None:
        topology = small_topology(y_lines=4, mcu_zone=True, mcu_links=3)
        self.assertEqual(topology, small_topology(y_lines=4, mcu_zone=True, mcu_links=3))
        self.assertEqual(topology, json.loads(json.dumps(topology)))
        clips = {
            node: attachment
            for node, attachment in topology["node_attachments"].items()
            if node.startswith("MCU_")
        }
        self.assertEqual(len(clips), 40)
        self.assertEqual(len({tuple(value) for value in clips.values()}), 40)
        self.assertEqual(clips["MCU_R0_C0"], ["MCU0", "X", 0])
        self.assertEqual(clips["MCU_R1_C19"], ["MCU0", "X", 39])
        self.assertEqual(topology["chips"][-1], {
            "name": "MCU0", "x_lines": 40, "y_lines": 3,
        })
        self.assertEqual(topology["inter_chip_links"][-1], [
            ["MCU0", "Y", 2], ["C0", "Y", 3],
        ])
        self.assertIs(topology["metadata"]["hardware_assumptions"][0]["verified"], False)
        self.assertEqual(topology["metadata"]["model"], "logical_simulation")

        graph = build_resource_graph(topology)
        fixed = {frozenset(edge) for edge in graph.fixed_edges}
        self.assertNotIn(frozenset(("MCU_R0_C0", "MCU_R0_C1")), fixed)

    def test_single_chip_bank_starts_at_zero(self) -> None:
        topology = small_topology(chips=1, y_lines=2, links_per_pair=9,
                                  mcu_zone=True, mcu_links=2)
        self.assertEqual(topology["inter_chip_links"], [
            [["MCU0", "Y", 0], ["C0", "Y", 0]],
            [["MCU0", "Y", 1], ["C0", "Y", 1]],
        ])

    def test_invalid_zone_dimensions_and_collisions_are_structured(self) -> None:
        calls = [
            lambda: small_topology(mcu_zone=1),
            lambda: small_topology(mcu_zone=True, clip_rows="2"),
            lambda: small_topology(mcu_zone=True, clip_rows=3),
            lambda: small_topology(mcu_zone=True, clips_per_row=0),
            lambda: small_topology(mcu_zone=True, mcu_links=0),
            lambda: small_topology(mcu_zone=True, mcu_links=4),
            lambda: small_topology(chips=1, x_lines=1, y_lines=1, links_per_pair=0,
                                   node_prefix="MCU_R0_C", mcu_zone=True),
        ]
        for call in calls:
            with self.subTest(call=call), self.assertRaises(RouteError) as caught:
                call()
            self.assertEqual(caught.exception.code, "INVALID_TOPOLOGY")


if __name__ == "__main__":
    unittest.main()
