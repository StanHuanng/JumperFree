"""Session/plan behavior against the JF2 memory model, never STM32 evidence."""

from dataclasses import replace
import unittest

from jumperfree.board import BoardProfile, SwitchOp, SwitchPlan, Xp, compile_plan, state_with_tags
from jumperfree.core import SCHEMA
from jumperfree.core.shadow import Shadow, apply_ops, shadow_crc
from jumperfree.host import DeviceClient, HostError
from jumperfree.host.mock_device import MockDevice
from jumperfree.routing.steiner import crossbar_create_net, crossbar_delete_net, crossbar_init_state


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class InterceptDevice(MockDevice):
    """Real MockDevice dispatch with injectable wire loss/corruption and bounded time."""

    def __init__(self, clock):
        self.clock = clock
        self.filter_reply = lambda request, reply: reply
        super().__init__(clock=lambda: clock.now * 1000)

    def _reply(self, line):
        return self.filter_reply(line, super()._reply(line))

    def readline(self):
        self.clock.now += 0.01
        return super().readline()


class DeviceClientTests(unittest.TestCase):
    def setUp(self):
        self.profile = BoardProfile.load()
        self.empty = crossbar_init_state(self.profile.topology)
        self.one = crossbar_create_net(self.empty, "change", ["Clip0", "Clip15"])
        self.plan = compile_plan(self.empty, self.one, self.profile)
        self.clock = Clock()
        self.device = InterceptDevice(self.clock)
        self.client = self.make_client()
        self.addCleanup(self.client.close)

    def make_client(self, **kwargs):
        return DeviceClient(self.device, topology_id=self.profile.topology_id,
                            clock=self.clock, frame_timeout=0.05, retries=0, **kwargs)

    def assert_error(self, result, kind, code):
        self.assertFalse(result.ok)
        self.assertEqual(result.kind, kind)
        self.assertEqual(result.error.code, code)
        self.assertFalse(result.verified)

    def assert_no_transaction(self, writes):
        self.assertFalse(any(line.startswith(b"JF2 TXN") for line in writes))
        self.assertFalse(any(b"ALLOPEN" in line or b"FAULT CLEAR" in line for line in self.device.writes))

    def test_requires_successful_init_and_close_is_idempotent(self):
        self.assert_error(self.client.apply(self.plan), "rejected", "NOT_INITIALIZED")
        self.assertEqual(self.device.writes, [])
        self.client.close()
        self.client.close()
        self.assertTrue(self.device.closed)
        self.assert_error(self.client.apply(self.plan), "transport", "SESSION_CLOSED")

    def test_empty_board_apply_and_post_status_match(self):
        initial = self.client.connect()
        self.assertEqual((initial.epoch, initial.state, initial.shadow), (0, "ARMED", Shadow()))
        result = self.client.apply(self.plan)
        self.assertTrue(result.ok, result.as_dict())
        self.assertEqual(result.evidence, "mock-device-applied")
        self.assertEqual(result.state_changed, "yes")
        self.assertFalse(result.verified)
        self.assertEqual((result.opened, result.closed), (0, len(self.plan.makes)))
        self.assertEqual(self.device.execution_log, self.plan.to_wire_tokens())
        status = self.client.read_status()
        self.assertEqual(status, result.status)
        self.assertEqual((status.epoch, status.crc), (1, self.plan.expect_crc))
        self.assertEqual(status.shadow, self.device.shadow)
        self.assertEqual(result.as_dict()["status"]["crc"], f"{self.plan.expect_crc:08x}")

    def test_modify_existing_connections_preserves_unrelated_net_and_bbm(self):
        first = crossbar_create_net(self.one, "keep", ["Clip1", "Clip2"])
        plan1 = compile_plan(self.empty, first, self.profile)
        self.client.connect()
        self.assertTrue(self.client.apply(plan1).ok)
        first = state_with_tags(first, plan1.tags)
        second = crossbar_create_net(crossbar_delete_net(first, "change"), "change", ["Clip0", "Clip14"])
        plan2 = compile_plan(first, second, self.profile, base_epoch=1)
        result = self.client.apply(plan2)
        self.assertTrue(result.ok, result.as_dict())
        self.assertEqual((result.status.epoch, result.status.crc), (2, plan2.expect_crc))
        self.assertEqual(self.device.execution_log, plan2.to_wire_tokens())
        self.assertTrue(plan2.breaks)
        self.assertTrue(plan2.makes)
        for chip, x, y in first["nets"]["keep"]["crosspoints"]:
            index = self.profile.chip_map[chip]
            self.assertTrue(self.device.shadow.closed[index] & (1 << (x * 8 + y)))
            self.assertEqual(self.device.shadow.tags[index * 128 + x * 8 + y], plan1.tags["keep"])
        self.assert_no_transaction([])

    def test_wrong_epoch_or_topology_rejects_without_mutation_and_allows_corrected_plan(self):
        self.client.connect()
        for plan, code in ((replace(self.plan, base_epoch=1), "EPOCH_MISMATCH"),
                           (replace(self.plan, topology_id="wrong"), "PLAN_TOPOLOGY_MISMATCH")):
            with self.subTest(code=code):
                start = len(self.device.writes)
                before = (self.device.epoch, self.device.shadow)
                self.assert_error(self.client.apply(plan), "rejected", code)
                self.assertEqual((self.device.epoch, self.device.shadow), before)
                self.assert_no_transaction(self.device.writes[start:])
        self.assertTrue(self.client.apply(self.plan).ok)

    def test_same_epoch_wrong_base_shadow_or_expect_crc_rejected(self):
        self.client.connect()
        self.assertTrue(self.client.apply(self.plan).ok)
        stale = replace(self.plan, base_epoch=1)
        # This plan assumes an empty base even though its epoch was edited to match.
        no_op = compile_plan(self.empty, self.empty, self.profile, base_epoch=1)
        correct_no_op = compile_plan(self.one, self.one, self.profile, base_epoch=1)
        for plan in (stale, no_op, replace(correct_no_op, expect_crc=0)):
            with self.subTest(plan=plan):
                before = (self.device.epoch, self.device.shadow)
                start = len(self.device.writes)
                self.assert_error(self.client.apply(plan), "rejected", "PLAN_STATE_MISMATCH")
                self.assertEqual((self.device.epoch, self.device.shadow), before)
                self.assert_no_transaction(self.device.writes[start:])

    def test_changed_break_tag_is_not_hidden_by_removing_the_connection(self):
        self.client.connect()
        self.assertTrue(self.client.apply(self.plan).ok)
        remove = compile_plan(self.one, self.empty, self.profile, base_epoch=1)
        wrong_tag = replace(remove, breaks=tuple(replace(op, tag=15) for op in remove.breaks))
        start = len(self.device.writes)
        self.assert_error(self.client.apply(wrong_tag), "rejected", "PLAN_STATE_MISMATCH")
        self.assertEqual(self.device.epoch, 1)
        self.assert_no_transaction(self.device.writes[start:])

    def test_large_plan_splits_add_frames_and_preserves_order(self):
        makes = tuple(SwitchOp(Xp(c, x, y), True, 1) for c in range(2) for x in range(8) for y in range(8))
        target = apply_ops(Shadow(), makes=makes)
        plan = SwitchPlan(SCHEMA["plan"], 0, self.profile.topology_id, (), makes,
                          shadow_crc(target), (), {"all": 1})
        self.client.connect()
        self.assertTrue(self.client.apply(plan).ok)
        adds = [line for line in self.device.writes if line.startswith(b"JF2 TXN ADD")]
        self.assertGreater(len(adds), 1)
        self.assertTrue(all(len(line) <= 512 for line in self.device.writes))
        self.assertEqual([int(line.split()[4]) for line in adds], list(range(len(adds))))
        self.assertEqual([token.decode() for line in adds for token in line.split()[5:]], plan.to_wire_tokens())
        self.assertEqual(self.device.execution_log, plan.to_wire_tokens())
        # Maximum-size transaction: retag all 128 points with breaks spanning frames.
        breaks = tuple(replace(op, on=False) for op in makes)
        retagged = tuple(replace(op, tag=2) for op in makes)
        target = apply_ops(target, breaks, retagged)
        change = SwitchPlan(SCHEMA["plan"], 1, self.profile.topology_id, breaks, retagged,
                            shadow_crc(target), (), {"all": 2})
        too_large = replace(change, makes=retagged + (retagged[0],))
        start = len(self.device.writes)
        self.assert_error(self.client.apply(too_large), "rejected", "PLAN_TOO_LARGE")
        self.assert_no_transaction(self.device.writes[start:])
        start = len(self.device.writes)
        self.assertTrue(self.client.apply(change).ok)
        self.assertEqual(self.device.execution_log, change.to_wire_tokens())
        self.assertTrue(all(len(line) <= 512 for line in self.device.writes[start:]))
        self.assertEqual(self.device.shadow, target)

    def test_noop_plan_uses_zero_add_frames_and_increments_epoch(self):
        self.client.connect()
        plan = compile_plan(self.empty, self.empty, self.profile)
        result = self.client.apply(plan)
        self.assertTrue(result.ok, result.as_dict())
        self.assertEqual((result.status.epoch, result.status.crc), (1, plan.expect_crc))
        self.assertEqual((result.opened, result.closed), (0, 0))
        self.assertFalse(any(b"TXN ADD" in line for line in self.device.writes))
        self.assertIn(b" 0 00000000\n", self.device.writes[-4])

    def test_profile_and_device_op_limits_reject_before_begin(self):
        for profile_limit, device_limit in ((1, "256"), (256, "1")):
            with self.subTest(profile_limit=profile_limit, device_limit=device_limit):
                self.device.filter_reply = lambda request, reply: (
                    reply.replace(b"value=256", f"value={device_limit}".encode())
                    if request == b"JF2 CFG GET max_ops\n" else reply)
                client = self.make_client(max_ops=profile_limit)
                client.connect()
                start = len(self.device.writes)
                self.assert_error(client.apply(self.plan), "rejected", "PLAN_TOO_LARGE")
                self.assert_no_transaction(self.device.writes[start:])

    def test_lost_commit_ack_recovers_through_query_without_resend(self):
        self.client.connect()
        self.device.filter_reply = lambda request, reply: b"" if request.startswith(b"JF2 TXN COMMIT") else reply
        result = self.client.apply(self.plan)
        self.assertTrue(result.ok, result.as_dict())
        self.assertTrue(result.recovered)
        self.assertEqual((result.status.epoch, result.status.crc), (1, self.plan.expect_crc))
        self.assertEqual(sum(b"TXN COMMIT" in line for line in self.device.writes), 1)
        self.assertEqual(sum(b"TXN QUERY" in line for line in self.device.writes), 1)

    def test_interrupted_commit_lost_ack_reports_aborted_and_latches_stop(self):
        self.client.connect()
        self.device.interrupt_next_commit()
        self.device.filter_reply = lambda request, reply: b"" if request.startswith(b"JF2 TXN COMMIT") else reply
        result = self.client.apply(self.plan)
        self.assert_error(result, "unresolved", "COMMIT_UNRESOLVED")
        self.assertEqual(result.error.details, {"state_changed": "unknown", "result": "ABORTED"})
        self.assertEqual((self.device.state, self.device.epoch, self.device.shadow), ("SAFE", 1, Shadow()))
        writes = list(self.device.writes)
        self.assert_error(self.client.apply(self.plan), "unresolved", "COMMIT_UNRESOLVED")
        with self.assertRaises(HostError):
            self.client.connect()
        self.assertEqual(self.device.writes, writes)

    def test_commit_unknown_or_query_timeout_stops_all_followups(self):
        self.client.connect()

        def lose_result(request, reply):
            if request.startswith(b"JF2 TXN COMMIT"):
                return b""
            if request.startswith(b"JF2 TXN QUERY"):
                return b"JF2 OK TXN QUERY " + request.split()[3] + b" result=UNKNOWN\n"
            return reply

        for query_timeout in (False, True):
            with self.subTest(query_timeout=query_timeout):
                client = self.make_client()
                self.device.filter_reply = lambda request, reply: reply
                client.connect()
                base = self.device.epoch
                plan = compile_plan(self.empty, self.empty, self.profile, base_epoch=base)
                self.device.filter_reply = (
                    (lambda request, reply: b"" if b"TXN COMMIT" in request or b"TXN QUERY" in request else reply)
                    if query_timeout else lose_result)
                result = client.apply(plan)
                self.assert_error(result, "unresolved", "TIMEOUT" if query_timeout else "COMMIT_UNRESOLVED")
                self.assertEqual(result.state_changed, "unknown")
                self.assertEqual(self.device.epoch, base + 1)  # It actually applied; never retry.
                writes = list(self.device.writes)
                self.assertEqual(client.apply(plan).as_dict(), result.as_dict())
                for operation in (client.connect, client.read_status):
                    with self.assertRaises(HostError):
                        operation()
                self.assertEqual(self.device.writes, writes)

    def test_safe_fault_or_staging_state_stops_without_begin(self):
        self.client.connect()
        for state in ("SAFE", "STAGING", "UNRECOGNIZED"):
            with self.subTest(state=state):
                client = self.make_client()
                client.connect()
                self.device.state = state
                start = len(self.device.writes)
                self.assert_error(client.apply(self.plan), "rejected", "STATE_MISMATCH")
                self.assert_no_transaction(self.device.writes[start:])
        client = self.make_client()
        client.connect()
        self.device.fault("hw")
        start = len(self.device.writes)
        self.assert_error(client.apply(self.plan), "rejected", "DEVICE_FAULT")
        writes = list(self.device.writes)
        self.assert_error(client.apply(self.plan), "rejected", "DEVICE_FAULT")
        self.assertEqual(self.device.writes, writes)
        self.assert_no_transaction(self.device.writes[start:])

    def test_hello_rejects_wrong_identity_capabilities_protocol_or_fault(self):
        for old, new, code in ((b"board=v01", b"board=other", "BOARD_MISMATCH"),
                               (b"proto=2", b"proto=1", "PROTOCOL_MISMATCH"),
                               (b"chips=2", b"chips=1", "BOARD_MISMATCH"),
                               (b"caps=led,sw,txn,lease", b"caps=led", "CAPABILITY_MISMATCH"),
                               (b"state=ARMED", b"state=UNKNOWN", "STATE_MISMATCH")):
            with self.subTest(code=code):
                client = self.make_client()
                self.device.filter_reply = lambda request, reply: reply.replace(old, new)
                with self.assertRaises(HostError) as caught:
                    client.connect()
                self.assertEqual(caught.exception.code, code)
                self.assert_error(client.apply(self.plan), "rejected", "NOT_INITIALIZED")
        self.device.filter_reply = lambda request, reply: reply
        self.device.fault("hw")
        with self.assertRaises(HostError) as caught:
            self.client.connect()
        self.assertEqual(caught.exception.code, "DEVICE_FAULT")
        self.assert_error(self.client.apply(self.plan), "rejected", "NOT_INITIALIZED")

    def test_crc_tags_inconsistency_rejected_before_staging(self):
        self.client.connect()
        self.device.filter_reply = lambda request, reply: (
            reply.replace(b"t0=" + b"0" * 64, b"t0=01" + b"0" * 62)
            if request == b"JF2 SW TAGS\n" else reply)
        start = len(self.device.writes)
        self.assert_error(self.client.apply(self.plan), "rejected", "SHADOW_MISMATCH")
        self.assert_no_transaction(self.device.writes[start:])

    def test_device_rejects_unsafe_transaction_and_original_code_is_preserved(self):
        # Host replays existing ops/CRC; the device owns line/tag electrical safety policy.
        makes = (SwitchOp(Xp(0, 0, 0), True, 1), SwitchOp(Xp(1, 0, 0), True, 2))
        plan = SwitchPlan(SCHEMA["plan"], 0, self.profile.topology_id, (), makes,
                          shadow_crc(apply_ops(Shadow(), makes=makes)), (), {"a": 1, "b": 2})
        self.client.connect()
        result = self.client.apply(plan)
        self.assert_error(result, "device_error", "DEVICE_ERROR")
        self.assertEqual(result.error.details["device_code"], "SAFETY_SHORT")
        self.assertEqual((self.device.epoch, self.device.shadow), (0, Shadow()))
        writes = list(self.device.writes)
        self.client.apply(plan)
        self.assertEqual(self.device.writes, writes)

    def test_timeout_transport_and_postcommit_mismatch_are_explicit(self):
        for failure, kind, code in (("timeout", "timeout", "TIMEOUT"),
                                    ("transport", "transport", "TRANSPORT_ERROR"),
                                    ("postcommit", "device_error", "CONFIRMATION_MISMATCH")):
            with self.subTest(failure=failure):
                self.device.filter_reply = lambda request, reply: reply
                client = self.make_client()
                client.connect()
                plan = compile_plan(self.empty, self.empty, self.profile, base_epoch=self.device.epoch)
                if failure == "timeout":
                    self.device.filter_reply = lambda request, reply: b""
                elif failure == "transport":
                    self.device.closed = True
                else:
                    self.device.filter_reply = lambda request, reply: (
                        reply.replace(b"opened=0", b"opened=1") if b"TXN COMMIT" in request else reply)
                result = client.apply(plan)
                self.assert_error(result, kind, code)
                self.assertEqual(result.state_changed, "yes" if failure == "postcommit" else "no")
                self.device.closed = False

    def test_postcommit_status_epoch_and_crc_must_match_plan(self):
        for field in ("epoch", "crc"):
            with self.subTest(field=field):
                client = self.make_client()
                self.device.filter_reply = lambda request, reply: reply
                client.connect()
                base = self.device.epoch
                plan = compile_plan(self.empty, self.empty, self.profile, base_epoch=base)

                def corrupt_status(request, reply):
                    if request == b"JF2 SW STATUS\n" and self.device.epoch == base + 1:
                        if field == "epoch":
                            return reply.replace(f"epoch={base + 1}".encode(), f"epoch={base + 2}".encode())
                        return reply.replace(f"crc={plan.expect_crc:08x}".encode(), b"crc=00000000")
                    return reply

                self.device.filter_reply = corrupt_status
                result = client.apply(plan)
                self.assert_error(result, "device_error",
                                  "CONFIRMATION_MISMATCH" if field == "epoch" else "SHADOW_MISMATCH")
                self.assertEqual(result.state_changed, "yes")
                writes = list(self.device.writes)
                client.apply(plan)
                self.assertEqual(self.device.writes, writes)

    def test_direct_commit_interruption_stops(self):
        self.client.connect()
        self.device.interrupt_next_commit()
        result = self.client.apply(self.plan)
        self.assert_error(result, "device_error", "DEVICE_ERROR")
        self.assertEqual(result.error.details["device_code"], "COMMIT_INTERRUPTED")
        self.assertEqual(result.state_changed, "yes")
        self.assertEqual(self.device.state, "SAFE")
        writes = list(self.device.writes)
        self.client.apply(self.plan)
        self.assertEqual(self.device.writes, writes)

    def test_fault_during_staging_sends_no_commit_or_later_plan(self):
        self.client.connect()

        def fault_after_begin(request, reply):
            if request.startswith(b"JF2 TXN BEGIN"):
                self.device.fault("hw")
            return reply

        self.device.filter_reply = fault_after_begin
        result = self.client.apply(self.plan)
        self.assert_error(result, "device_error", "DEVICE_ERROR")
        self.assertEqual(result.error.details["device_code"], "SAFE_MODE")
        self.assertEqual(self.device.state, "SAFE")
        self.assertFalse(any(b"TXN COMMIT" in line for line in self.device.writes))
        writes = list(self.device.writes)
        self.client.apply(self.plan)
        self.assertEqual(self.device.writes, writes)

    def test_shadow_snapshot_must_be_stable_across_status_and_tags(self):
        self.client.connect()

        def drift_during_read(request, reply):
            if request == b"JF2 SW TAGS\n":
                self.device.epoch += 1
            return reply

        self.device.filter_reply = drift_during_read
        start = len(self.device.writes)
        self.assert_error(self.client.apply(self.plan), "rejected", "STATE_MISMATCH")
        self.assert_no_transaction(self.device.writes[start:])
