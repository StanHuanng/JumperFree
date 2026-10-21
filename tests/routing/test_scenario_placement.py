from __future__ import annotations

import copy
import unittest

from jumperfree.routing.model import RouteError
from jumperfree.routing.scenarios import (
    Scenario,
    load_scenario,
    scenario_to_route_requests,
    scenario_to_topology,
)


def placement_scenario(modules: list[dict[str, object]]) -> dict[str, object]:
    first = modules[0]
    first_pin = next(iter(first["pins"]))  # type: ignore[arg-type]
    return {
        "id": "placement-test",
        "title": "Placement test",
        "topology": {
            "chips": 2, "x_lines": 4, "y_lines": 4, "links_per_pair": 1,
            "clip_rows": 2, "clips_per_row": 20, "mcu_links": 1,
        },
        "mcu": {"kind": "adapter", "pins": {"A": {"row": 1, "col": 19}}},
        "modules": modules,
        "nets": [{"id": "only", "members": ["mcu.A", f"{first['id']}.{first_pin}"]}],
        "sources": ["https://example.com/placement"],
        "assumptions": [],
        "expected": {"routeable": True},
    }


class ScenarioPlacementTests(unittest.TestCase):
    def assert_code(self, data: dict[str, object], code: str) -> RouteError:
        with self.assertRaises(RouteError) as caught:
            load_scenario(data)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.details["scenario_id"], "placement-test")
        return caught.exception

    def test_absolute_rows_are_reserved_before_relative_and_null_pins(self) -> None:
        data = placement_scenario([
            {
                "id": "a_relative", "kind": "relative",
                "placement": {"row_range": [0, 4]},
                "pins": {"Z_REL": {"offset": 0}, "B_NULL": None, "A_NULL": None},
            },
            {
                "id": "z_fixed", "kind": "fixed",
                "placement": {"rows": [0]}, "pins": {"FIXED": 0},
            },
        ])
        loaded = load_scenario(data)
        self.assertEqual(loaded.endpoint_nodes["z_fixed.FIXED"], "N0")
        self.assertEqual(loaded.endpoint_nodes["a_relative.Z_REL"], "N1")
        self.assertEqual(loaded.endpoint_nodes["a_relative.A_NULL"], "N2")
        self.assertEqual(loaded.endpoint_nodes["a_relative.B_NULL"], "N3")
        self.assertEqual(loaded.endpoint_nodes["mcu.A"], "MCU_R1_C19")

    def test_all_relative_anchors_are_reserved_before_null_pins(self) -> None:
        data = placement_scenario([
            {
                "id": "a", "kind": "A", "placement": {"rows": [0, 1, 2]},
                "pins": {"P": None},
            },
            {
                "id": "b", "kind": "B", "placement": {"rows": [0, 1]},
                "pins": {"P": {"offset": 0}, "Q": {"offset": 1}},
            },
        ])
        data["mcu"]["pins"]["B"] = {"row": 0, "col": 0}  # type: ignore[index]
        data["nets"] = [
            {"id": "a-net", "members": ["mcu.A", "a.P"]},
            {"id": "b-net", "members": ["mcu.B", "b.P"]},
        ]

        loaded = load_scenario(data)

        self.assertEqual(loaded.endpoint_nodes["b.P"], "N0")
        self.assertEqual(loaded.endpoint_nodes["b.Q"], "N1")
        self.assertEqual(loaded.endpoint_nodes["a.P"], "N2")

    def test_reordered_input_has_identical_normalized_placement_and_requests(self) -> None:
        data = placement_scenario([
            {"id": "beta", "kind": "part", "placement": {"rows": [4, 3]},
             "pins": {"B": None, "A": None}},
            {"id": "alpha", "kind": "part", "placement": {"row_range": [0, 2]},
             "pins": {"P": {"offset": 1}}},
        ])
        data["nets"] = [{"id": "z", "members": ["mcu.A", "beta.B"]}]
        reordered = copy.deepcopy(data)
        reordered["modules"] = list(reversed(reordered["modules"]))  # type: ignore[arg-type]
        for module in reordered["modules"]:  # type: ignore[union-attr]
            module["pins"] = dict(reversed(list(module["pins"].items())))
        left, right = load_scenario(data), load_scenario(reordered)
        self.assertEqual(left.endpoint_nodes, right.endpoint_nodes)
        self.assertEqual(scenario_to_route_requests(left), scenario_to_route_requests(right))
        self.assertEqual(scenario_to_topology(left), scenario_to_topology(right))

    def test_placement_out_of_range_variants(self) -> None:
        cases = [
            [{"id": "x", "kind": "x", "placement": {"rows": [8]}, "pins": {"A": 8}}],
            [{"id": "x", "kind": "x", "placement": {"rows": [0]}, "pins": {"A": 1}}],
            [{"id": "x", "kind": "x", "placement": {"rows": [0]},
              "pins": {"A": None, "B": None}}],
            [{"id": "x", "kind": "x", "placement": {"rows": [0, 1]},
              "pins": {"A": {"offset": 2}}}],
        ]
        for modules in cases:
            with self.subTest(modules=modules):
                self.assert_code(placement_scenario(modules), "PLACEMENT_OUT_OF_RANGE")

    def test_conflicting_placement_variants(self) -> None:
        cases = [
            [
                {"id": "a", "kind": "a", "placement": {"rows": [0]}, "pins": {"A": 0}},
                {"id": "b", "kind": "b", "placement": {"rows": [0]}, "pins": {"B": 0}},
            ],
            [{"id": "x", "kind": "x", "placement": {"rows": [0, 1]},
              "pins": {"A": 0, "B": 0}}],
            [{"id": "x", "kind": "x", "placement": {"row_range": [0, 2]},
              "pins": {"A": {"offset": 0}, "B": {"offset": 0}}}],
            [
                {"id": "a", "kind": "a", "placement": {"rows": [0]}, "pins": {"A": 0}},
                {"id": "b", "kind": "b", "placement": {"rows": [0, 1]},
                 "pins": {"B": {"offset": 0}, "C": None}},
            ],
            [
                {"id": "a", "kind": "a", "placement": {"rows": [0, 1]},
                 "pins": {"A": 0, "B": 1}},
                {"id": "b", "kind": "b", "placement": {"rows": [0, 1]},
                 "pins": {"C": {"offset": 0}}},
            ],
        ]
        for modules in cases:
            with self.subTest(modules=modules):
                self.assert_code(placement_scenario(modules), "CONFLICTING_PIN_PLACEMENT")

    def test_front_range_is_honored_and_override_must_keep_resolved_rows(self) -> None:
        data = placement_scenario([{
            "id": "oled", "kind": "display", "placement": {"row_range": [4, 7]},
            "pins": {"GND": {"offset": 0}, "VCC": {"offset": 1},
                     "SCL": {"offset": 2}, "SDA": {"offset": 3}},
        }])
        loaded = load_scenario(data)
        self.assertEqual(
            {loaded.endpoint_nodes[f"oled.{pin}"] for pin in ("GND", "VCC", "SCL", "SDA")},
            {"N4", "N5", "N6", "N7"},
        )
        with self.assertRaises(RouteError) as caught:
            scenario_to_topology(loaded, chips=1, x_lines=7)
        self.assertEqual(caught.exception.code, "PLACEMENT_OUT_OF_RANGE")
        expanded = scenario_to_topology(loaded, chips=1, x_lines=8, y_lines=2,
                                        links_per_pair=0, mcu_links=2)
        self.assertEqual(len(expanded["node_attachments"]), 48)

    def test_adapters_reject_non_scenario_and_builder_rejects_bad_override(self) -> None:
        for call in (
            lambda: scenario_to_topology("bad"),
            lambda: scenario_to_route_requests("bad"),
        ):
            with self.subTest(call=call), self.assertRaises(RouteError) as caught:
                call()  # type: ignore[arg-type]
            self.assertEqual(caught.exception.code, "INVALID_SCENARIO")
        loaded = load_scenario(placement_scenario([
            {"id": "x", "kind": "x", "placement": {"rows": [0]}, "pins": {"A": 0}}
        ]))
        with self.assertRaises(RouteError) as caught:
            scenario_to_topology(loaded, chips=True)
        self.assertEqual(caught.exception.code, "INVALID_TOPOLOGY")


if __name__ == "__main__":
    unittest.main()
