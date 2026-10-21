from __future__ import annotations

import copy
import unittest
from pathlib import Path

from jumperfree.routing.crossbar import build_resource_graph, find_shorts
from jumperfree.routing.model import RouteError
from jumperfree.routing.scenarios import (
    Scenario,
    load_scenario,
    scenario_to_route_requests,
    scenario_to_topology,
)
from jumperfree.routing.steiner import crossbar_create_net, crossbar_init_state

ROOT = Path(__file__).resolve().parents[2]
LIBRARY = ROOT / "examples" / "routing" / "scenarios"
FIXTURES = ROOT / "tests" / "routing" / "fixtures" / "scenarios"
PROFILE_P = {
    "chips": 2, "x_lines": 16, "y_lines": 24, "links_per_pair": 8,
    "clip_rows": 2, "clips_per_row": 20, "mcu_links": 16,
}


def route(scenario: Scenario) -> tuple[dict[str, object], dict[str, object]]:
    topology = scenario_to_topology(scenario)
    state = crossbar_init_state(topology)
    for net_id, nodes in scenario_to_route_requests(scenario):
        state = crossbar_create_net(state, net_id, nodes)
    return topology, state


def assert_net_connected(
    case: unittest.TestCase, topology: dict[str, object], net: dict[str, object]
) -> None:
    graph = build_resource_graph(topology)
    adjacent: dict[object, set[object]] = {vertex: set() for vertex in graph.vertices}
    for left, right in graph.fixed_edges:
        adjacent[left].add(right)
        adjacent[right].add(left)
    for chip, x_line, y_line in net["crosspoints"]:  # type: ignore[assignment]
        left, right = (chip, "X", x_line), (chip, "Y", y_line)
        adjacent[left].add(right)
        adjacent[right].add(left)
    nodes = net["nodes"]  # type: ignore[assignment]
    reached = {nodes[0]}
    pending = [nodes[0]]
    while pending:
        vertex = pending.pop()
        for neighbor in adjacent[vertex]:
            if neighbor not in reached:
                reached.add(neighbor)
                pending.append(neighbor)
    case.assertTrue(set(nodes).issubset(reached))


class ScenarioLibraryTests(unittest.TestCase):
    def test_every_positive_library_scenario_routes_deterministically(self) -> None:
        paths = sorted(LIBRARY.glob("*.json"))
        self.assertEqual(len(paths), 9)
        for path in paths:
            with self.subTest(path=path.name):
                scenario = load_scenario(path)
                self.assertTrue(scenario.expected.routeable)
                self.assertEqual({
                    name: getattr(scenario.topology, name) for name in PROFILE_P
                }, PROFILE_P)
                self.assertTrue(all(source.startswith(("http://", "https://"))
                                    for source in scenario.sources))
                self.assertTrue(any("provisional" in assumption.lower()
                                    for assumption in scenario.assumptions))

                topology, state = route(scenario)
                requests = scenario_to_route_requests(scenario)
                self.assertEqual(set(state["nets"]), {net_id for net_id, _ in requests})
                for net_id, _ in requests:
                    assert_net_connected(self, topology, state["nets"][net_id])
                points = [
                    point for net in state["nets"].values() for point in net["crosspoints"]
                ]
                assignments = {net_id: nodes for net_id, nodes in requests}
                self.assertEqual(find_shorts(topology, points, assignments), [])

                modules = {module.id: module for module in scenario.modules}
                for endpoint, node in scenario.endpoint_nodes.items():
                    owner, _ = endpoint.split(".")
                    if owner == "mcu":
                        self.assertTrue(node.startswith("MCU_R"))
                    else:
                        self.assertTrue(node.startswith("N"))
                        self.assertIn(int(node[1:]), modules[owner].placement.rows)

                second_topology, second_state = route(load_scenario(path))
                self.assertEqual(second_topology, topology)
                self.assertEqual(second_state, state)

    def test_default_capacity_negative_fails_atomically_and_repeatably(self) -> None:
        scenario = load_scenario(FIXTURES / "no-free-path.json")
        self.assertFalse(scenario.expected.routeable)
        self.assertEqual(scenario.expected.error_code, "NO_FREE_PATH")
        self.assertIn("one MCU trunk", scenario.expected.known_limitation or "")
        self.assertIn("Expected failing net: second", scenario.assumptions)
        topology = scenario_to_topology(scenario)

        failures: list[dict[str, object]] = []
        for _ in range(2):
            state = crossbar_init_state(topology)
            for net_id, nodes in scenario_to_route_requests(scenario):
                before = copy.deepcopy(state)
                try:
                    state = crossbar_create_net(state, net_id, nodes)
                except RouteError as exc:
                    self.assertEqual(net_id, "second")
                    self.assertEqual(exc.code, scenario.expected.error_code)
                    self.assertEqual(state, before)
                    failures.append(exc.as_dict())
                    break
            else:
                self.fail("negative scenario unexpectedly routed")
            points = [
                point for net in state["nets"].values() for point in net["crosspoints"]
            ]
            assignments = {
                name: net["nodes"] for name, net in state["nets"].items()
            }
            self.assertEqual(find_shorts(topology, points, assignments), [])
        self.assertEqual(failures[0], failures[1])

    def test_negative_validation_fixtures_have_exact_codes(self) -> None:
        codes = {
            "conflicting-pin-placement.json": "CONFLICTING_PIN_PLACEMENT",
            "invalid-scenario.json": "INVALID_SCENARIO",
            "mcu-pin-outside.json": "MCU_PIN_OUTSIDE_CLIP_ZONE",
            "placement-out-of-range.json": "PLACEMENT_OUT_OF_RANGE",
            "unknown-pin.json": "UNKNOWN_PIN",
        }
        self.assertEqual(
            {path.name for path in FIXTURES.glob("*.json")} - {"no-free-path.json"},
            set(codes),
        )
        for name, code in codes.items():
            with self.subTest(name=name), self.assertRaises(RouteError) as caught:
                load_scenario(FIXTURES / name)
            self.assertEqual(caught.exception.code, code)


if __name__ == "__main__":
    unittest.main()
