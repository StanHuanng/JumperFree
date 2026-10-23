"""P1–P3: seeded, small PR workloads with real routing and rearrange."""

import copy
import random
import unittest

from jumperfree.board import (BoardProfile, compile_plan, shadow_from_state,
                              state_crosspoints_from_shadow, state_with_tags, verify_plan)
from jumperfree.board.stats import v01_transitions
from jumperfree.core import canonical_json
from jumperfree.core.shadow import apply_ops
from jumperfree.routing.crossbar import find_shorts
from jumperfree.routing.rearrange import crossbar_create_net_with_rearrange
from tests.board.test_plan import congested_state


class PlanPropertyTests(unittest.TestCase):
    def check_transition(self, old, new, profile):
        saved = copy.deepcopy((old, new))
        plan = compile_plan(old, new, profile)
        target = state_with_tags(new, plan.tags)
        view = verify_plan(old, plan, profile)
        expected = {name: {'crosspoints': net['crosspoints']} for name, net in new['nets'].items()}
        self.assertEqual(view, {'nets': expected, 'meta': {'tags': dict(plan.tags)}})  # P1
        self.assertEqual(canonical_json(plan.to_json()),
                         canonical_json(compile_plan(old, new, profile).to_json()))  # P3
        image = shadow_from_state(old, profile)
        target_image = shadow_from_state(target, profile)
        original = image.closed
        tag_names = {f'tag-{tag}': tag for tag in range(1, 16)}
        for index, op in enumerate((*plan.breaks, *plan.makes)):
            image = apply_ops(image, [op] if not op.on else (), [op] if op.on else ())
            # P2: before makes every image is a subset of old; afterwards a subset of target.
            bound = original if index < len(plan.breaks) else target_image.closed
            for chip in range(2):
                self.assertEqual(image.closed[chip] & ~bound[chip], 0)
                self.assertEqual(image.closed[chip] & ~(original[chip] | target_image.closed[chip]), 0)
            intermediate = state_crosspoints_from_shadow(image, profile, tag_names)
            points = [point for net in intermediate['nets'].values() for point in net['crosspoints']]
            assignments = {}
            for node, (chip_name, _, x) in profile.topology['node_attachments'].items():
                chip = profile.chip_map[chip_name]
                tags = {image.tags[chip * 128 + x * 8 + y] for y in range(8)
                        if image.closed[chip] & (1 << (x * 8 + y))}
                self.assertLessEqual(len(tags), 1)
                if tags:
                    assignments[node] = str(next(iter(tags)))
            self.assertEqual(find_shorts(profile.topology, points, assignments), [])
        self.assertEqual(image, target_image)
        self.assertEqual((old, new), saved)
        return plan

    def test_seeded_router_deletes_creates_and_arbitrary_snapshots(self):
        profile = BoardProfile.load()
        for seed in (0, 7, 42):
            transitions = list(v01_transitions(profile, seed=seed, trials=25))
            for old, new in transitions:
                self.check_transition(old, new, profile)
            rng = random.Random(seed)
            snapshots = [transitions[0][0]] + [new for _, new in transitions]
            for _ in range(10):
                self.check_transition(rng.choice(snapshots), rng.choice(snapshots), profile)

    def test_actual_rearrange_marks_disturbed_victim(self):
        profile = BoardProfile.load()
        old = congested_state(profile)
        new, stats = crossbar_create_net_with_rearrange(old, 'new', ['Clip2', 'Clip3'], max_moves=64)
        self.assertTrue(stats['rearrange_attempted'])
        self.assertEqual(stats['rearranged_nets'], ['old'])
        plan = self.check_transition(old, new, profile)
        self.assertEqual(plan.disturbed_nets, ('old',))
