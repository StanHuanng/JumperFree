"""plan_net_change is a read-only dry run: no state change, no device I/O."""

import copy
import unittest
from unittest import mock

from jumperfree.agent import (
    MockAgentSession, TOOL_DESCRIPTIONS, TOOL_SCHEMAS,
    create_net, delete_net, get_state, modify_net, plan_net_change,
)
from jumperfree.host import ApplyResult, HostError


class PlanNetChangeTests(unittest.TestCase):
    def setUp(self):
        self.session = MockAgentSession()
        self.addCleanup(self.session.close)
        create_net(self.session, "A", ["Clip1", "Clip8"])
        create_net(self.session, "B", ["Clip2", "Clip9"])

    def snapshot(self):
        dev = self.session._device
        return (get_state(self.session), copy.deepcopy(dev.shadow), dev.epoch,
                list(dev.writes), self.session._control._epoch)

    def test_plan_leaves_state_epoch_shadow_and_wire_unchanged(self):
        cases = (("create", "C", ["Clip3", "Clip10"]), ("modify", "A", ["Clip1", "Clip11"]),
                 ("delete", "B", None))
        for action, name, nodes in cases:
            with self.subTest(action=action):
                before = self.snapshot()
                with mock.patch.object(self.session._client, "apply") as apply:
                    result = plan_net_change(self.session, action, name, nodes)
                    apply.assert_not_called()
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["operation"], "plan")
                self.assertIsNone(result["netlist"])
                self.assertIsNone(result["apply"])
                self.assertFalse(result["verified"])
                self.assertEqual(self.snapshot(), before)

    def test_plan_reports_counts_and_occupied_nodes(self):
        result = plan_net_change(self.session, "create", "C", ["Clip3", "Clip10"])
        plan = result["plan"]
        self.assertEqual(plan["action"], "create")
        self.assertEqual(plan["breaks"], 0)
        self.assertGreater(plan["makes"], 0)
        self.assertEqual(plan["base_epoch"], self.session._control._epoch)
        self.assertEqual(plan["occupied_nodes"],
                         ["Clip1", "Clip10", "Clip2", "Clip3", "Clip8", "Clip9"])
        self.assertEqual({n["net"] for n in result["router"]["nets"]}, {"A", "B", "C"})
        deleted = plan_net_change(self.session, "delete", "B")["plan"]
        self.assertGreater(deleted["breaks"], 0)
        self.assertEqual(deleted["makes"], 0)

    def test_planned_counts_match_later_execution(self):
        plan = plan_net_change(self.session, "modify", "A", ["Clip1", "Clip10"])["plan"]
        applied = modify_net(self.session, "A", ["Clip1", "Clip10"])["apply"]
        self.assertEqual((plan["breaks"], plan["makes"]), (applied["opened"], applied["closed"]))

    def test_failures_match_direct_execution_and_do_not_touch_state(self):
        cases = (
            ("create", "A", ["Clip3", "Clip4"], create_net),
            ("create", "C", ["Clip3", "Clip99"], create_net),
            ("create", "C", ["Clip3", "Clip3"], create_net),
            ("modify", "Z", ["Clip3", "Clip4"], modify_net),
            ("delete", "Z", None, delete_net),
            ("create", " ", ["Clip3", "Clip4"], create_net),
            ("create", "C", "Clip3", create_net),
        )
        for action, name, nodes, direct in cases:
            with self.subTest(action=action, name=name, nodes=nodes):
                before = self.snapshot()
                planned = plan_net_change(self.session, action, name, nodes)
                self.assertEqual(self.snapshot(), before)
                args = (name,) if action == "delete" else (name, nodes)
                executed = direct(self.session, *args)
                self.assertFalse(planned["ok"])
                self.assertFalse(executed["ok"])
                self.assertEqual(planned["error"], executed["error"])
                self.assertEqual(planned["router"], executed["router"])
                self.assertNotIn("plan", planned)

    def test_unknown_action_and_closed_session(self):
        bad = plan_net_change(self.session, "rename", "A", None)
        self.assertEqual(bad["error"]["code"], "BAD_ARG")
        self.session.close()
        closed = plan_net_change(self.session, "bogus", "", None)
        self.assertEqual(closed["error"]["code"], "SESSION_CLOSED")

    def test_blocked_session_rejects_plan_like_a_write(self):
        failure = ApplyResult("timeout", error=HostError("TIMEOUT", "t"))
        with mock.patch.object(self.session._client, "apply", return_value=failure):
            create_net(self.session, "C", ["Clip3", "Clip10"])
        direct = create_net(self.session, "D", ["Clip4", "Clip11"])
        planned = plan_net_change(self.session, "create", "D", ["Clip4", "Clip11"])
        self.assertEqual(direct["error"]["code"], "CONTROL_BLOCKED")
        self.assertEqual(planned["error"], direct["error"])
        self.assertIsNone(planned["netlist"])

    def test_schema_registered(self):
        self.assertIn("plan_net_change", TOOL_SCHEMAS)
        self.assertIn("plan_net_change", TOOL_DESCRIPTIONS)
        schema = TOOL_SCHEMAS["plan_net_change"]["inputSchema"]
        self.assertEqual(schema["required"], ["action", "name"])
        self.assertEqual(schema["properties"]["action"]["enum"], ["create", "modify", "delete"])


if __name__ == "__main__":
    unittest.main()
