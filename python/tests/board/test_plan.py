import copy
from dataclasses import FrozenInstanceError, replace
import unittest
from unittest.mock import patch

from jumperfree.board import (BoardProfile, Limits, PlanError, SwitchOp, SwitchPlan, Xp,
                              compile_plan, net_tags, shadow_from_state,
                              state_crosspoints_from_shadow, state_with_tags, verify_plan)
from jumperfree.core import JfError, SCHEMA, canonical_json
from jumperfree.core.shadow import Shadow, apply_ops, shadow_crc
from jumperfree.routing.model import RouteError
from jumperfree.routing.steiner import crossbar_init_state, crossbar_create_net


def congested_state(profile):
    state = crossbar_init_state(profile.topology)
    points = [['C1', 0, y] for y in range(8)] + [['C1', 1, 0]]
    lines = [['C1', 'X', 0], ['C1', 'X', 1]] + [
        [chip, 'Y', y] for chip in ('C1', 'C2') for y in range(8)]
    state['nets']['old'] = {'nodes': ['Clip0', 'Clip1'], 'lines': sorted(lines), 'crosspoints': points}
    return state


class TypeTests(unittest.TestCase):
    def test_ranges_tokens_and_immutable_canonical_order(self):
        self.assertEqual(Xp(1, 7, 7).bit(), 63)
        for args in ((2, 0, 0), (-1, 0, 0), (0, 8, 0), (0, 15, 0), (0, 0, 8), (True, 0, 0)):
            with self.subTest(args=args), self.assertRaises(JfError) as caught:
                Xp(*args)
            self.assertEqual(caught.exception.code, 'BAD_ARG')
        for xp, on, tag in ((None, True, 1), (Xp(0, 0, 0), 1, 1), (Xp(0, 0, 0), True, 16)):
            with self.assertRaises(JfError):
                SwitchOp(xp, on, tag)
        op1, op2 = SwitchOp(Xp(1, 7, 7), False, 0), SwitchOp(Xp(0, 0, 0), False, 15)
        make1, make2 = SwitchOp(op1.xp, True, 15), SwitchOp(op2.xp, True, 1)
        tags = {'b': 15, 'a': 1}
        plan = SwitchPlan(SCHEMA['plan'], 2, '0' * 16, (op1, op2), (make1, make2), 0, ('z', 'a', 'z'), tags)
        tags['a'] = 4
        self.assertEqual(plan.to_wire_tokens(), ['-0:0:0', '-1:7:7', '+0:0:0@1', '+1:7:7@15'])
        self.assertEqual(plan.to_json()['disturbed_nets'], ['a', 'z'])
        self.assertEqual(plan.tags['a'], 1)
        with self.assertRaises(TypeError):
            plan.tags['a'] = 2
        with self.assertRaises(FrozenInstanceError):
            plan.expect_crc = 2
        with self.assertRaises(JfError):
            replace(plan, schema='wrong')
        for key, value in (('base_epoch', -1), ('base_epoch', True), ('base_epoch', 1 << 32), ('expect_crc', 1 << 32)):
            with self.assertRaises(JfError):
                replace(plan, **{key: value})


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.profile = BoardProfile.load()
        self.empty = crossbar_init_state(self.profile.topology)
        self.one = crossbar_create_net(self.empty, 'z', ['Clip0', 'Clip15'])

    def test_roundtrip_no_mutation_empty_and_noop(self):
        original = copy.deepcopy(self.one)
        plan = compile_plan(self.empty, self.one, self.profile, base_epoch=12)
        self.assertEqual(plan.base_epoch, 12)
        self.assertEqual((len(plan.breaks), len(plan.makes)), (0, 2))
        view = verify_plan(self.empty, plan, self.profile)
        self.assertEqual(view['nets']['z']['crosspoints'], self.one['nets']['z']['crosspoints'])
        self.assertEqual(view['meta']['tags'], {'z': 1})
        tagged = state_with_tags(self.one, plan.tags)
        self.assertEqual(shadow_from_state(tagged, self.profile).shadow_crc(), plan.expect_crc)
        self.assertEqual(self.one, original)
        noop = compile_plan(tagged, tagged, self.profile)
        self.assertEqual(noop.to_wire_tokens(), [])
        cleared = compile_plan(tagged, self.empty, self.profile)
        self.assertEqual(len(cleared.breaks), 2)
        self.assertEqual(verify_plan(tagged, cleared, self.profile), {'nets': {}, 'meta': {'tags': {}}})
        self.assertEqual(compile_plan(self.empty, self.empty, self.profile).expect_crc, Shadow().shadow_crc())

    def test_bbm_prefixes_preserve_tag_ownership_during_net_swap(self):
        old = crossbar_create_net(self.empty, 'a', ['Clip0', 'Clip1'])
        old = crossbar_create_net(old, 'b', ['Clip2', 'Clip3'])
        old = crossbar_create_net(old, 'keep', ['Clip4', 'Clip5'])
        new = crossbar_create_net(self.empty, 'b', ['Clip0', 'Clip1'])
        new = crossbar_create_net(new, 'a', ['Clip2', 'Clip3'])
        new = crossbar_create_net(new, 'keep', ['Clip4', 'Clip5'])
        plan = compile_plan(old, new, self.profile)
        self.assertEqual(plan.disturbed_nets, ('a', 'b'))
        self.assertEqual((len(plan.breaks), len(plan.makes)), (4, 4))
        image = shadow_from_state(old, self.profile)
        for op in plan.breaks + plan.makes:
            image = apply_ops(image, breaks=(op,)) if not op.on else apply_ops(image, makes=(op,))
            state_crosspoints_from_shadow(image, self.profile, plan.tags)
        self.assertEqual(image, shadow_from_state(state_with_tags(new, plan.tags), self.profile))
        self.assertEqual(shadow_crc(image), plan.expect_crc)

    def test_tuple_crosspoints_match_list_crosspoints(self):
        tuples = copy.deepcopy(self.one)
        tuples['nets']['z']['crosspoints'] = [tuple(point) for point in
                                               tuples['nets']['z']['crosspoints']]
        self.assertEqual(compile_plan(self.empty, tuples, self.profile),
                         compile_plan(self.empty, self.one, self.profile))
        noop = compile_plan(self.one, tuples, self.profile)
        self.assertEqual(noop.to_wire_tokens(), [])
        self.assertEqual(noop.disturbed_nets, ())

    def test_break_tags_match_old_shadow_or_are_unspecified(self):
        plan = compile_plan(self.one, self.empty, self.profile)
        for tag in (0, 1):
            changed = replace(plan, breaks=tuple(replace(op, tag=tag) for op in plan.breaks))
            self.assertEqual(verify_plan(self.one, changed, self.profile)['nets'], {})
        changed = replace(plan, breaks=tuple(replace(op, tag=2) for op in plan.breaks))
        with self.assertRaises(PlanError) as caught:
            verify_plan(self.one, changed, self.profile)
        self.assertEqual(caught.exception.code, 'PLAN_VERIFY_FAILED')

    def test_shadow_short_on_x_line_without_external_node(self):
        topology = self.profile.topology
        del topology['node_attachments']['Clip8']
        profile = replace(self.profile, topology_json=canonical_json(topology))
        image = apply_ops(Shadow(), makes=(SwitchOp(Xp(0, 0, 0), True, 1),
                                          SwitchOp(Xp(1, 0, 0), True, 2)))
        with self.assertRaises(PlanError) as caught:
            state_crosspoints_from_shadow(image, profile, {'a': 1, 'b': 2})
        self.assertEqual(caught.exception.code, 'PLAN_VERIFY_FAILED')
        separate = apply_ops(Shadow(), makes=(SwitchOp(Xp(0, 0, 0), True, 1),
                                             SwitchOp(Xp(1, 0, 1), True, 2)))
        view = state_crosspoints_from_shadow(separate, profile, {'a': 1, 'b': 2})
        self.assertEqual(view['nets']['b']['crosspoints'], [['C2', 0, 1]])

    def test_stable_tags_smallest_free_lexical_and_metadata_preserved(self):
        old = crossbar_create_net(self.one, 'b', ['Clip1', 'Clip2'])
        self.assertEqual(net_tags(old), {'b': 1, 'z': 2})
        old = state_with_tags(old, {'b': 7, 'z': 3})
        old['meta']['unknown'] = 'preserved'
        new = crossbar_create_net(old, 'a', ['Clip3', 'Clip4'])
        new['meta']['tags']['a'] = 12
        plan = compile_plan(old, new, self.profile)
        self.assertEqual(dict(plan.tags), {'a': 1, 'b': 7, 'z': 3})
        self.assertEqual(len(plan.makes), 2)
        self.assertEqual(plan.breaks, ())
        persisted = state_with_tags(new, plan.tags)
        self.assertEqual(persisted['meta']['unknown'], 'preserved')
        # Stale IDs left by routing deletes are ignored, including their values.
        persisted['meta']['tags']['deleted'] = 'unknown'
        self.assertEqual(net_tags(persisted), dict(plan.tags))
        partial = copy.deepcopy(old)
        del partial['meta']['tags']['z']
        self.assertEqual(net_tags(partial), {'b': 7, 'z': 1})
        renamed = copy.deepcopy(self.one)
        renamed['nets']['a'] = renamed['nets'].pop('z')
        rename_plan = compile_plan(self.one, renamed, self.profile)
        self.assertEqual((len(rename_plan.breaks), len(rename_plan.makes)), (2, 2))
        self.assertEqual(rename_plan.disturbed_nets, ())

    def test_tag_errors(self):
        for tags in ({'a': 0}, {'a': True}, {'a': 16}, {'a': 1, 'b': 1}, {1: 1}):
            with self.assertRaises(PlanError):
                state_crosspoints_from_shadow(Shadow(), self.profile, tags)
        with self.assertRaises(PlanError) as caught:
            net_tags({'nets': {str(i): {} for i in range(16)}})
        self.assertEqual(caught.exception.code, 'PLAN_TOO_LARGE')
        for old, new in ((self.empty, {'nets': {str(i): {} for i in range(16)}}),
                         ({'nets': {str(i): {} for i in range(16)}}, self.empty)):
            with self.assertRaises(PlanError) as caught:
                compile_plan(old, new, self.profile)
            self.assertEqual(caught.exception.code, 'PLAN_TOO_LARGE')
        for meta in (None, {'tags': []}):
            with self.assertRaises(RouteError):
                net_tags({'nets': {}, 'meta': meta})
        with self.assertRaises(PlanError):
            state_with_tags(self.one, {})
        with self.assertRaises(RouteError):
            state_with_tags({'nets': {}, 'meta': None}, {})

    def test_topology_routing_range_and_badpoint_errors(self):
        other = copy.deepcopy(self.empty)
        other['topology']['metadata']['extra'] = True
        for old, new in ((self.empty, other), (other, self.empty)):
            with self.assertRaises(PlanError) as caught:
                compile_plan(old, new, self.profile)
            self.assertEqual(caught.exception.code, 'PLAN_TOPOLOGY_MISMATCH')
        with self.assertRaises(PlanError) as caught:
            compile_plan(other, other, self.profile)
        self.assertEqual(caught.exception.code, 'PLAN_BOARD_MISMATCH')
        with self.assertRaises(RouteError):
            compile_plan({}, self.empty, self.profile)
        unwired = copy.deepcopy(self.one)
        unwired['nets']['z']['crosspoints'] = [['C1', 8, 0]]
        with self.assertRaises(JfError) as caught:
            compile_plan(self.empty, unwired, self.profile)
        self.assertEqual(caught.exception.code, 'BAD_ARG')
        point = Xp(0, 0, 0)
        prof = replace(self.profile, known_bad_crosspoints=(point,))
        with self.assertRaises(PlanError) as caught:
            compile_plan(self.empty, self.one, prof)
        self.assertEqual(caught.exception.code, 'PLAN_BAD_CROSSPOINT')
        duplicate = crossbar_create_net(self.one, 'a', ['Clip1', 'Clip2'])
        duplicate['nets']['a']['crosspoints'] = copy.deepcopy(duplicate['nets']['z']['crosspoints'])
        with self.assertRaises(PlanError) as caught:
            shadow_from_state(duplicate, self.profile)
        self.assertEqual(caught.exception.code, 'PLAN_VERIFY_FAILED')

    def test_too_many_ops_and_replay_failures(self):
        plan = compile_plan(self.empty, self.one, self.profile)
        with self.assertRaises(PlanError) as caught:
            compile_plan(self.empty, self.one, replace(self.profile, limits=Limits(max_ops=1)))
        self.assertEqual(caught.exception.code, 'PLAN_TOO_LARGE')
        op = plan.makes[0]
        off = replace(op, on=False)
        for changed in (replace(plan, expect_crc=plan.expect_crc ^ 1), replace(plan, topology_id='f'*16),
                        replace(plan, breaks=(off,)), replace(plan, makes=plan.makes + (op,)),
                        replace(plan, breaks=(op,)), replace(plan, tags={'z': 2})):
            with self.subTest(plan=changed.to_json()), self.assertRaises(PlanError) as caught:
                verify_plan(self.empty, changed, self.profile)
            self.assertEqual(caught.exception.code, 'PLAN_VERIFY_FAILED')
        with self.assertRaises(PlanError):
            verify_plan(self.one, plan, self.profile)

    def test_inverse_rejects_open_tags_unknown_tags_x_collision_and_short(self):
        for ops, tags in (([SwitchOp(Xp(0, 0, 0), True, 0)], {'z': 1}),
                          ([SwitchOp(Xp(0, 0, 0), True, 1), SwitchOp(Xp(0, 0, 1), True, 2)], {'a': 1, 'b': 2}),
                          ([SwitchOp(Xp(0, 0, 0), True, 1), SwitchOp(Xp(1, 0, 0), True, 2)], {'a': 1, 'b': 2})):
            image = apply_ops(Shadow(), makes=ops)
            with self.assertRaises(PlanError) as caught:
                state_crosspoints_from_shadow(image, self.profile, tags)
            self.assertEqual(caught.exception.code, 'PLAN_VERIFY_FAILED')
        image = Shadow(tags=bytes([1]) + bytes(255))
        with self.assertRaises(PlanError):
            state_crosspoints_from_shadow(image, self.profile, {'a': 1})
        with self.assertRaises(JfError):
            state_crosspoints_from_shadow(Shadow((1 << 64, 0)), self.profile, {})

    def test_compiler_independent_target_and_declared_short_checks(self):
        # Deliberately corrupt replay's returned view: compiler must compare to target.
        with patch('jumperfree.board.plan.verify_plan', return_value={'nets': {}}):
            with self.assertRaises(PlanError) as caught:
                compile_plan(self.empty, self.one, self.profile)
            self.assertEqual(caught.exception.code, 'PLAN_VERIFY_FAILED')
        # Physical ownership can agree while declared routing memberships disagree.
        new = crossbar_create_net(self.one, 'a', ['Clip1', 'Clip2'])
        new['nets']['z']['crosspoints'].append(['C1', 1, 0])
        new['nets']['z']['crosspoints'].sort()
        new['nets']['a']['crosspoints'] = []
        with self.assertRaises(PlanError) as caught:
            compile_plan(self.empty, new, self.profile)
        self.assertEqual(caught.exception.code, 'PLAN_VERIFY_FAILED')
