"""guide_leds / all_open over the agent session (Mock evidence only)."""

import unittest

from jumperfree.agent import (
    MockAgentSession, all_open, create_net, delete_net, get_state, guide_leds,
    modify_net, plan_net_change,
)
from jumperfree.board import GUIDE_PALETTE
from jumperfree.host import HostError


class GuideLedsTests(unittest.TestCase):
    def setUp(self):
        self.session = MockAgentSession()
        self.addCleanup(self.session.close)
        self.dev = self.session._device

    def test_batch_frame_covers_all_sixteen_clips(self):
        create_net(self.session, "A", ["Clip1", "Clip8"])
        create_net(self.session, "B", ["Clip2", "Clip9"])
        self.dev.leds[5] = (9, 9, 9)  # stale guidance must be cleared
        start = len(self.dev.writes)
        result = guide_leds(self.session)
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["verified"])
        self.assertEqual(result["led"]["count"], 16)
        frames = [w for w in self.dev.writes[start:] if b"LED BATCH" in w]
        self.assertEqual(len(frames), 1)
        tokens = frames[0].decode().split()
        self.assertEqual(tokens[:4], ["JF2", "LED", "BATCH", "16"])
        self.assertEqual(len(tokens) - 4, 16)
        expected = [(0, 0, 0)] * 16
        for clip in (1, 8):
            expected[clip] = GUIDE_PALETTE[0]
        for clip in (2, 9):
            expected[clip] = GUIDE_PALETTE[1]
        self.assertEqual(self.dev.leds, expected)

    def test_plan_input_lights_candidate_without_applying(self):
        create_net(self.session, "A", ["Clip1", "Clip8"])
        epoch, crc = self.dev.epoch, self.dev.shadow.shadow_crc()
        start = len(self.dev.writes)
        result = guide_leds(self.session, "create", "B", ["Clip2", "Clip9"])
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["verified"])
        self.assertEqual(result["led"]["source"], "plan")
        self.assertEqual(result["led"]["count"], 16)
        self.assertEqual([w for w in self.dev.writes[start:] if b"LED BATCH" not in w], [])
        expected = [(0, 0, 0)] * 16
        for clip in (1, 8):
            expected[clip] = GUIDE_PALETTE[0]
        for clip in (2, 9):
            expected[clip] = GUIDE_PALETTE[1]
        self.assertEqual(self.dev.leds, expected)
        self.assertEqual((self.dev.epoch, self.dev.shadow.shadow_crc()), (epoch, crc))
        self.assertEqual(set(self.nets()), {"A"})  # confirmed state untouched
        self.assertEqual(guide_leds(self.session)["led"]["source"], "confirmed")

    def test_plan_input_delete_and_errors(self):
        create_net(self.session, "A", ["Clip1", "Clip8"])
        self.assertTrue(guide_leds(self.session, "delete", "A")["ok"])
        self.assertEqual(self.dev.leds, [(0, 0, 0)] * 16)
        self.dev.leds[3] = (7, 7, 7)
        start = len(self.dev.writes)
        for args in (("create", "B", ["Clip1", "Clip9"]), ("create", "B", ["Nope", "Clip9"]),
                     ("bogus", "B", None), (None, "B", ["Clip2", "Clip9"]), ("create", " ", None),
                     ("create", "B", None)):
            with self.subTest(args=args):
                result = guide_leds(self.session, *args)
                self.assertFalse(result["ok"])
        self.assertEqual(self.dev.writes[start:], [])
        self.assertEqual(self.dev.leds[3], (7, 7, 7))

    def test_plan_input_safe_mode_passes_through(self):
        self.dev.fault("hw")
        result = guide_leds(self.session, "create", "B", ["Clip2", "Clip9"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["details"]["device_code"], "SAFE_MODE")
        self.assertEqual(self.dev.state, "SAFE")

    def nets(self):
        return {n["net"]: n for n in get_state(self.session)["netlist"]["nets"]}

    def test_empty_netlist_blanks_all_leds(self):
        self.dev.leds[3] = (1, 2, 3)
        self.assertTrue(guide_leds(self.session)["ok"])
        self.assertEqual(self.dev.leds, [(0, 0, 0)] * 16)

    def test_no_switch_change(self):
        create_net(self.session, "A", ["Clip1", "Clip8"])
        before = (self.dev.epoch, self.dev.shadow.shadow_crc())
        guide_leds(self.session)
        self.assertEqual((self.dev.epoch, self.dev.shadow.shadow_crc()), before)

    def test_safe_mode_passes_through_without_rearm(self):
        create_net(self.session, "A", ["Clip1", "Clip8"])
        self.dev.fault("hw")
        self.assertEqual(self.dev.state, "SAFE")
        start = len(self.dev.writes)
        result = guide_leds(self.session)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["details"]["device_code"], "SAFE_MODE")
        self.assertFalse(any(b"HELLO" in w for w in self.dev.writes[start:]))
        self.assertEqual(self.dev.state, "SAFE")

    def test_led_batch_validation_is_all_or_nothing(self):
        client = self.session._client
        before = list(self.dev.leds)
        start = len(self.dev.writes)
        bad = ([(0, (1, 2, 3)), (16, (0, 0, 0))], [(0, (1, 2, 3)), (0, (4, 5, 6))],
               [(0, (1, 2, 3)), (1, (256, 0, 0))], [(0, (1, 2))], [(True, (1, 2, 3))], [5])
        for updates in bad:
            with self.subTest(updates=updates):
                with self.assertRaises(HostError) as ctx:
                    client.led_batch(updates)
                self.assertEqual(ctx.exception.code, "BAD_ARG")
        self.assertEqual(self.dev.leds, before)
        self.assertEqual(self.dev.writes[start:], [])

    def test_led_batch_wire_format_and_empty_batch(self):
        client = self.session._client
        start = len(self.dev.writes)
        self.assertEqual(client.led_batch({15: (255, 0, 10), 0: (1, 2, 3)}), 2)
        self.assertEqual(self.dev.writes[start], b"JF2 LED BATCH 2 15:ff000a 0:010203\n")
        self.assertEqual(client.led_batch([]), 0)
        self.assertEqual(self.dev.writes[start + 1], b"JF2 LED BATCH 0\n")

    def test_existing_operations_still_work(self):
        self.assertTrue(create_net(self.session, "A", ["Clip1", "Clip8"])["ok"])
        self.assertTrue(guide_leds(self.session)["ok"])
        self.assertTrue(modify_net(self.session, "A", ["Clip1", "Clip9"])["ok"])
        self.assertTrue(plan_net_change(self.session, "delete", "A")["ok"])
        self.assertTrue(delete_net(self.session, "A")["ok"])
        self.assertTrue(get_state(self.session)["ok"])


class AllOpenTests(unittest.TestCase):
    def setUp(self):
        self.session = MockAgentSession()
        self.addCleanup(self.session.close)
        self.dev = self.session._device

    def test_all_open_leaves_empty_safe_device_and_unconfirmed_state(self):
        create_net(self.session, "A", ["Clip1", "Clip8"])
        epoch = self.dev.epoch
        result = all_open(self.session)
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["verified"])
        self.assertEqual(self.dev.state, "SAFE")
        self.assertEqual(self.dev.epoch, epoch + 1)
        self.assertFalse(any(self.dev.shadow.closed))
        state = get_state(self.session)
        self.assertFalse(state["ok"])
        self.assertIsNone(state["netlist"])
        self.assertEqual(state["error"]["code"], "STATE_UNCONFIRMED")

    def test_writes_plans_and_guides_refused_after_all_open(self):
        all_open(self.session)
        start = len(self.dev.writes)
        results = (create_net(self.session, "B", ["Clip2", "Clip9"]),
                   modify_net(self.session, "B", ["Clip2", "Clip9"]),
                   delete_net(self.session, "B"),
                   plan_net_change(self.session, "create", "B", ["Clip2", "Clip9"]),
                   guide_leds(self.session))
        for result in results:
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "SESSION_REESTABLISH_REQUIRED")
        self.assertEqual(self.dev.writes[start:], [])

    def test_acknowledge_cannot_bypass_gate(self):
        all_open(self.session)
        self.session._control.acknowledge_unresolved()
        self.assertEqual(create_net(self.session, "B", ["Clip2", "Clip9"])["error"]["code"],
                         "SESSION_REESTABLISH_REQUIRED")

    def test_all_open_in_safe_and_repeatable(self):
        self.dev.fault("hw")
        self.assertEqual(self.dev.state, "SAFE")
        self.assertTrue(all_open(self.session)["ok"])
        self.assertTrue(all_open(self.session)["ok"])

    def test_all_open_in_staging(self):
        client = self.session._client
        epoch = self.dev.epoch
        client._session.request(("TXN", "BEGIN", "77", str(epoch), "1"))
        self.assertEqual(self.dev.state, "STAGING")
        self.assertTrue(all_open(self.session)["ok"])
        self.assertEqual(self.dev.state, "SAFE")

    def test_client_requires_new_hello_after_all_open(self):
        client = self.session._client
        client.all_open()
        self.assertIsNone(client.status)
        self.assertEqual(self.dev.state, "SAFE")
        status = client.connect()  # explicit HELLO re-arms a fault-free device
        self.assertEqual(status.state, "ARMED")
        self.assertEqual(status.epoch, self.dev.epoch)

    def test_all_open_after_close(self):
        self.session.close()
        self.assertEqual(all_open(self.session)["error"]["code"], "SESSION_CLOSED")
        self.assertEqual(guide_leds(self.session)["error"]["code"], "SESSION_CLOSED")


if __name__ == "__main__":
    unittest.main()
