from __future__ import annotations

import copy
import json
import unittest
from dataclasses import FrozenInstanceError

from jumperfree.board import GUIDE_PALETTE, LedPlan, LedPlanError, compile_led_plan, plan_guide
from jumperfree.routing.steiner import crossbar_create_net, crossbar_init_state
from jumperfree.routing.v01_topology import v01_topology
from tests.guide_fixtures import EXAMPLE_PLAN, example_state


class GuidePlanTests(unittest.TestCase):
    def test_empty_state_has_no_lit_clips(self) -> None:
        self.assertEqual(plan_guide(crossbar_init_state(v01_topology())), [])

    def test_deterministic_plan_and_no_mutation(self) -> None:
        state = example_state()
        original = copy.deepcopy(state)
        self.assertEqual(plan_guide(state), EXAMPLE_PLAN)
        self.assertEqual(plan_guide(state), EXAMPLE_PLAN)
        self.assertEqual(state, original)
        state["nets"] = dict(reversed(list(state["nets"].items())))
        self.assertEqual(plan_guide(json.loads(json.dumps(state))), EXAMPLE_PLAN)
        # Creation history and route resources do not affect the color plan.
        recreated = crossbar_init_state(v01_topology())
        for name in ("alpha", "zeta"):
            recreated = crossbar_create_net(recreated, name, state["nets"][name]["nodes"])
        self.assertEqual(plan_guide(recreated), EXAMPLE_PLAN)

    def test_eight_nets_have_distinct_nonblack_fixed_colors(self) -> None:
        expected_colors = (
            (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0),
            (255, 0, 255), (0, 255, 255), (255, 128, 0), (128, 0, 255),
        )
        self.assertEqual(GUIDE_PALETTE, expected_colors)
        state = crossbar_init_state(v01_topology())
        for index in reversed(range(8)):
            state = crossbar_create_net(
                state, f"net{index}", [f"Clip{index}", f"Clip{index + 8}"],
            )
        plan = plan_guide(state)
        self.assertEqual(plan, [
            (clip, *expected_colors[clip % 8]) for clip in range(16)
        ])
        self.assertEqual(len({entry[1:] for entry in plan}), 8)
        self.assertNotIn((0, 0, 0), {entry[1:] for entry in plan})

    def test_rejects_unsupported_nodes_even_without_nets(self) -> None:
        for node in ("N0", "Clip16", "Clip-1", "Clip00", "clip0", "Clip１"):
            with self.subTest(node=node):
                topology = v01_topology()
                topology["node_attachments"][node] = topology["node_attachments"].pop("Clip0")
                with self.assertRaises(LedPlanError) as raised:
                    plan_guide(crossbar_init_state(topology))
                self.assertEqual(raised.exception.as_dict(), {
                    "code": "UNSUPPORTED_TOPOLOGY",
                    "message": "guide requires Clip0..Clip15 node IDs",
                    "details": {"nodes": [node]},
                })

    def test_malformed_states_are_board_errors(self) -> None:
        invalid_version = crossbar_init_state(v01_topology())
        invalid_version["version"] = 999
        invalid_net = example_state()
        invalid_net["nets"]["alpha"]["nodes"] = ["Clip16", "Clip2"]
        overlap = example_state()
        overlap["nets"]["zeta"]["nodes"] = ["Clip0", "Clip2"]
        invalid_topology = crossbar_init_state(v01_topology())
        invalid_topology["topology"] = {}
        for state in (None, [], {}, invalid_version, invalid_net, overlap, invalid_topology):
            with self.subTest(state=state), self.assertRaises(LedPlanError) as raised:
                plan_guide(state)
            self.assertEqual(raised.exception.code, "INVALID_STATE")


    def test_led_plan_records_unscaled_colors_and_brightness_placeholder(self) -> None:
        plan = compile_led_plan(example_state())
        self.assertIsInstance(plan, LedPlan)
        self.assertEqual(plan.brightness_cap, 64)
        self.assertEqual(dict(plan.frames), {clip: (r, g, b) for clip, r, g, b in EXAMPLE_PLAN})
        self.assertEqual(list(plan.frames), [entry[0] for entry in EXAMPLE_PLAN])
        with self.assertRaises(FrozenInstanceError):
            plan.brightness_cap = 1
        with self.assertRaises(TypeError):
            plan.frames[0] = (0, 0, 0)
        self.assertEqual(LedPlan({}, brightness_cap=32).brightness_cap, 32)

    def test_custom_palette_capacity_and_lexical_assignment(self) -> None:
        palette = [(1, 2, 3), (254, 253, 252)]
        original = copy.deepcopy(palette)
        plan = compile_led_plan(example_state(), palette)
        self.assertEqual(dict(plan.frames), {
            0: (254, 253, 252), 2: (1, 2, 3), 8: (1, 2, 3),
            10: (1, 2, 3), 15: (254, 253, 252),
        })
        self.assertEqual(palette, original)
        self.assertEqual(dict(compile_led_plan(crossbar_init_state(v01_topology()), ()).frames), {})
        for palette in ((), ((1, 2, 3),)):
            with self.subTest(palette=palette), self.assertRaises(LedPlanError) as raised:
                compile_led_plan(example_state(), palette)
            self.assertEqual(raised.exception.as_dict(), {
                "code": "INVALID_PALETTE",
                "message": "guide palette must contain at least one color per net",
                "details": {"required": 2, "available": len(palette)},
            })
