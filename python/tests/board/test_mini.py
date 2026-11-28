"""JumperFree mini (PCB v0.4, firmware board=mini): profile, topology, wire format."""

import json
import unittest
import zlib

from jumperfree.board import BoardProfile, TAG_PALETTE, compile_led_plan, compile_plan, net_tags
from jumperfree.board.profile import BOARDS_DIR
from jumperfree.core.geometry import V01, MINI
from jumperfree.core.shadow import Shadow, shadow_crc
from jumperfree.host import DeviceClient, HostError
from jumperfree.host.jf2 import commit_crc, parse_switch_op
from jumperfree.host.mock_device import MockDevice
from jumperfree.routing.mini_topology import mini_nodes, mini_topology
from jumperfree.routing.steiner import crossbar_create_net, crossbar_init_state

# Captured from the real board on COM4 (2026-11-28, empty matrix, SAFE).
REAL_INFO = (b"JF2 OK INFO fw=0.1.0 proto=2 caps=led,sw,txn,lease chips=7 board=mini boot=241f0cbc "
             b"epoch=0 state=SAFE reset=por brightness_cap=64 lease_default_ms=0 max_ops=256\n")
REAL_EMPTY_CRC = 0x32c5f669


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.profile = BoardProfile.for_board("mini")

    def test_packaged_files_match_the_generator(self):
        directory = BOARDS_DIR / "mini"
        self.assertEqual(json.loads((directory / "topology.json").read_text(encoding="utf-8")), mini_topology())
        self.assertEqual(json.loads((directory / "nodes.json").read_text(encoding="utf-8")), mini_nodes())
        self.assertEqual(BoardProfile.for_board("mini").wire_board, "mini")
        with self.assertRaises(ValueError):  # PlanError BOARD_UNSUPPORTED
            BoardProfile.for_board("v09")

    def test_clip_names_rows_leds_and_hidden_reserved_nodes(self):
        nodes = self.profile.nodes
        clips = [name for name, node in nodes.items() if node["kind"] == "clip"]
        self.assertEqual(sorted(clips), sorted([f"A{c}" for c in range(1, 25)] + [f"B{c}" for c in range(1, 25)]))
        self.assertEqual(sorted(self.profile.led_map.values()), list(range(48)))
        # FLIP0 is the back-row (A) clip at column 24; FLIP40 the front-row (B) clip at column 1.
        self.assertEqual((nodes["A24"]["flip"], nodes["A24"]["chip"], nodes["A24"]["y"]), (0, "U301", 0))
        self.assertEqual((nodes["B1"]["flip"], nodes["B1"]["chip"], nodes["B1"]["y"]), (40, "U306", 0))
        self.assertEqual(set(self.profile.public_nodes) - set(clips), {"TEST_SRC", "SVC_GND"})
        self.assertEqual(nodes["SVC_GND"]["y"], 7)
        self.assertEqual(self.profile.geometry, MINI)
        self.assertEqual(dict(self.profile.chip_map)["U307"], 6)

    def test_net_uses_one_bus_and_one_switch_per_clip(self):
        empty = crossbar_init_state(self.profile.topology)
        state = crossbar_create_net(empty, "SDA", ["A24", "B1", "SVC_GND"])
        plan = compile_plan(empty, state, self.profile)
        self.assertEqual(plan.to_wire_tokens(), ["+0:0:0@1", "+5:0:0@1", "+6:0:7@1"])
        self.assertEqual(plan.breaks, ())
        for token in plan.to_wire_tokens():
            parse_switch_op(token, MINI)

    def test_led_colors_follow_tags(self):
        empty = crossbar_init_state(self.profile.topology)
        state = crossbar_create_net(empty, "B", ["A1", "TEST_SRC"])
        state = crossbar_create_net(state, "A", ["A2", "A3"])
        tags = {"B": 2, "A": 1}
        frames = compile_led_plan(state, TAG_PALETTE, self.profile.led_map, tags).frames
        self.assertEqual(frames[self.profile.led_map["A1"]], TAG_PALETTE[1])
        self.assertEqual(frames[self.profile.led_map["A2"]], TAG_PALETTE[0])
        self.assertEqual(len(frames), 3)  # TEST_SRC has no LED


class WireTests(unittest.TestCase):
    def test_geometry_ranges_and_service_chip(self):
        for token in ("+6:15:6@1", "-6:0:7", "+5:15:7@15"):
            parse_switch_op(token, MINI)
        for token in ("+6:0:5@1", "+7:0:0@1", "+0:16:0@1", "+0:01:0@1"):
            with self.subTest(token=token), self.assertRaises(HostError):
                parse_switch_op(token, MINI)
        with self.assertRaises(HostError):
            parse_switch_op("+2:0:0@1")  # v01 default keeps c 0..1
        with self.assertRaises(HostError):
            commit_crc(["+0:8:0@1"], V01)
        self.assertEqual(commit_crc(["+0:8:0@1"], MINI), zlib.crc32(b"+0:8:0@1\n"))

    def test_empty_seven_chip_shadow_crc_matches_real_board(self):
        self.assertEqual(shadow_crc(Shadow.empty(7)), REAL_EMPTY_CRC)
        self.assertEqual(MockDevice(geometry=MINI).shadow.shadow_crc(), REAL_EMPTY_CRC)

    def test_mock_mini_replies_like_the_real_board(self):
        device = MockDevice(clock=lambda: 0, geometry=MINI)
        device.write(b"JF2 INFO\n")
        info = device.readline()
        self.assertIn(b"chips=7 board=mini", info)
        device.write(b"JF2 SW TAGS\n")
        tags = device.readline()
        self.assertEqual(len(tags), 939)  # same length as the board's reply

    def test_mock_mini_safety_shares_bus_not_clip(self):
        device = MockDevice(clock=lambda: 0, geometry=MINI)
        client = DeviceClient(device, topology_id="t", board_id="mini", geometry=MINI)
        client.connect()
        same_bus_two_tags = ["+0:0:0@1", "+1:0:0@2"]
        device.write(b"JF2 TXN BEGIN 9 0 2\n")
        device.write(("JF2 TXN ADD 9 0 " + " ".join(same_bus_two_tags) + "\n").encode())
        device.write(f"JF2 TXN COMMIT 9 1 {commit_crc(same_bus_two_tags, MINI):08x}\n".encode())
        replies = [device.readline() for _ in range(5)]
        self.assertTrue(any(b"SAFETY_SHORT" in line for line in replies), replies)

    def test_client_reads_long_mini_status_and_tags(self):
        device = MockDevice(clock=lambda: 0, geometry=MINI)
        client = DeviceClient(device, topology_id="t", board_id="mini", geometry=MINI)
        status = client.connect()
        self.assertEqual(status.crc, REAL_EMPTY_CRC)
        self.assertEqual(sorted(status.as_dict()), ["c0", "c1", "c2", "c3", "c4", "c5", "c6", "crc", "epoch", "state"])
        self.assertEqual(client.led_batch({i: (1, 2, 3) for i in range(48)}), 48)
        self.assertEqual(device.leds[47], (1, 2, 3))
        with self.assertRaises(HostError):
            client.led_batch({48: (0, 0, 0)})
        up, fault = client.ping(1)
        self.assertEqual((up, fault), (0, 0))
        client.lease(5000)
        self.assertEqual(device.lease_ms, 5000)

    def test_client_refuses_other_board(self):
        client = DeviceClient(MockDevice(clock=lambda: 0), topology_id="t", board_id="mini", geometry=MINI)
        with self.assertRaises(HostError) as raised:
            client.connect()
        self.assertEqual(raised.exception.code, "BOARD_MISMATCH")


if __name__ == "__main__":
    unittest.main()
