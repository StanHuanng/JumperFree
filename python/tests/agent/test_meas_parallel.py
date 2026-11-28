"""JumperFree mini: JF2 MEAS (mock), measure_voltage tool, parallel BUS for current_ma nets."""

import unittest

from jumperfree.agent import AgentSession, mock_opener
from jumperfree.core.geometry import MINI, V01
from jumperfree.host import DeviceClient, HostError
from jumperfree.host.jf2 import commit_crc
from jumperfree.host.mock_device import MockDevice
from jumperfree.routing.mini_topology import mini_nodes, mini_topology
from jumperfree.routing.model import RouteError
from jumperfree.routing.steiner import crossbar_create_net, crossbar_init_state, crossbar_query
from jumperfree.routing.v01_topology import v01_topology
from jumperfree.service.control import paths_for

FLIP = {name: node["flip"] for name, node in mini_nodes().items() if node["kind"] == "clip"}


def ask(device, line):
    device.write((line + "\n").encode())
    return device.readline().decode().strip()


class MeasWireTests(unittest.TestCase):
    def test_mock_meas_follows_the_firmware_rules(self):
        device = MockDevice(clock=lambda: 0, geometry=MINI)
        self.assertIn("caps=led,sw,txn,lease,meas", ask(device, "JF2 INFO"))
        self.assertEqual(ask(device, "JF2 MEAS 3 0"), "JF2 ERR SAFE_MODE HELLO required without faults")
        ask(device, "JF2 HELLO 2 ab")
        self.assertEqual(ask(device, "JF2 MEAS 3 0"), "JF2 OK MEAS 3 mv=2500 min=2500 max=2500 n=1 bus=15 temp=1")
        self.assertEqual(ask(device, "JF2 MEAS 48 0"), "JF2 ERR BAD_ARG invalid clip or window")
        self.assertEqual(ask(device, "JF2 MEAS 0 1001"), "JF2 ERR BAD_ARG invalid clip or window")
        self.assertEqual(ask(device, "JF2 MEAS 0"), "JF2 ERR BAD_FRAME incorrect argument count")
        ask(device, f"JF2 TXN BEGIN 7 {device.epoch} 1")
        self.assertEqual(ask(device, "JF2 MEAS 3 0"), "JF2 ERR BUSY transaction open")
        ask(device, "JF2 TXN ABORT 7")
        self.assertEqual(device.epoch, 0)

    def test_v01_has_no_meas(self):
        device = MockDevice(clock=lambda: 0, geometry=V01)
        self.assertNotIn("meas", ask(device, "JF2 INFO"))
        ask(device, "JF2 HELLO 2 ab")
        self.assertEqual(ask(device, "JF2 MEAS 3 0"), "JF2 ERR UNSUPPORTED no sensing hardware")

    def test_client_measure_uses_the_net_bus_and_leaves_the_shadow(self):
        device = MockDevice(clock=lambda: 0, geometry=MINI)
        client = DeviceClient(device, topology_id="t", board_id="mini", geometry=MINI)
        client.connect()
        # BUS 2: clip 0 (U301 Y0) with TEST_SRC (U307 Y6), one tag.
        ops = ["+0:2:0@1", "+6:2:6@1"]
        ask(device, f"JF2 TXN BEGIN 1 {device.epoch} 2")
        ask(device, "JF2 TXN ADD 1 0 " + " ".join(ops))
        ask(device, f"JF2 TXN COMMIT 1 1 {commit_crc(ops, MINI):08x}")
        before = (device.epoch, device.shadow)
        reading = client.measure(0, 100)
        self.assertEqual((reading["mv"], reading["bus"], reading["temp"]), (3300, 2, False))
        self.assertEqual(reading["samples"], 800)
        self.assertEqual((device.epoch, device.shadow), before)
        device.clip_mv[9] = -1200
        self.assertEqual(client.measure(9)["mv"], -1200)
        with self.assertRaises(HostError) as caught:
            client.measure(48)
        self.assertEqual(caught.exception.code, "BAD_ARG")


class ParallelRouteTests(unittest.TestCase):
    def test_mini_parallel_net_closes_one_switch_per_bus_per_clip(self):
        state = crossbar_init_state(mini_topology())
        state = crossbar_create_net(state, "SIG", ["A1", "A5"])
        state = crossbar_create_net(state, "VCC", ["A3", "A19", "B7"], parallel=3)
        net = crossbar_query(state, "VCC")
        buses = sorted({x for _, x, _ in net["crosspoints"]})
        self.assertEqual(net["parallel"], 3)
        self.assertEqual(len(buses), 3)
        self.assertNotIn(crossbar_query(state, "SIG")["crosspoints"][0][1], buses)
        self.assertEqual(len(net["crosspoints"]), 3 * 3)  # 3 clips x 3 BUS

    def test_parallel_needs_enough_free_bus(self):
        state = crossbar_init_state(mini_topology())
        for index in range(14):
            state = crossbar_create_net(state, f"N{index}", [f"A{index + 1}", f"B{index + 1}"])
        with self.assertRaises(RouteError) as caught:
            crossbar_create_net(state, "VCC", ["A20", "A21"], parallel=3)
        self.assertEqual(caught.exception.code, "NO_FREE_PATH")
        state = crossbar_create_net(state, "VCC", ["A20", "A21"], parallel=2)
        with self.assertRaises(RouteError) as caught:
            crossbar_create_net(state, "X", ["A22", "A23"], parallel=0)
        self.assertEqual(caught.exception.code, "INVALID_PARALLEL")

    def test_v01_parallel_uses_extra_common_channels(self):
        state = crossbar_init_state(v01_topology())
        state = crossbar_create_net(state, "P", ["Clip0", "Clip9"], parallel=2)
        net = crossbar_query(state, "P")
        self.assertEqual(len({y for _, _, y in net["crosspoints"]}), 2)

    def test_paths_for_current(self):
        self.assertEqual([paths_for(v) for v in (None, 1, 15, 16, 30, 31, 100)], [1, 1, 1, 2, 2, 3, 7])
        for value, code in ((0, "BAD_ARG"), (True, "BAD_ARG"), (101, "CURRENT_TOO_HIGH")):
            with self.assertRaises(HostError) as caught:
                paths_for(value)
            self.assertEqual(caught.exception.code, code)


class AgentToolTests(unittest.TestCase):
    def setUp(self):
        self.device = MockDevice(clock=lambda: 0, geometry=MINI)
        self.session = AgentSession(mock_opener("mini", self.device), heartbeat_s=None)
        self.addCleanup(self.session.close)

    def ok(self, result):
        self.assertTrue(result["ok"], result.get("error"))
        return result

    def change(self, action, name, nodes=None, current_ma=None):
        plan = self.ok(self.session.plan(action, name, nodes, current_ma))
        self.ok(self.session.guide(plan["plan_id"]))
        return plan, self.ok(self.session.execute(plan["plan_id"]))

    def test_power_net_with_current_gets_parallel_bus_on_the_device(self):
        plan, done = self.change("create", "3V3", ["A3", "A19"], current_ma=40)
        self.assertEqual(plan["plan"]["parallel_paths"], 3)
        self.assertEqual(plan["plan"]["path_ohm_typical"], 33)
        self.assertEqual(plan["plan"]["max_ma"], 45)
        net = done["netlist"]["nets"][0]
        self.assertEqual((net["parallel"], net["current_ma"]), (3, 40))
        closed = sum(bin(chip).count("1") for chip in self.device.shadow.closed)
        self.assertEqual(closed, 6)
        tags = {tag for tag in self.device.shadow.tags if tag}
        self.assertEqual(len(tags), 1)  # one net = one tag on all three BUS

    def test_plan_id_depends_on_current_and_modify_resets_it(self):
        first = self.ok(self.session.plan("create", "VCC", ["A3", "A19"], 20))
        second = self.ok(self.session.plan("create", "VCC", ["A3", "A19"]))
        self.assertNotEqual(first["plan_id"], second["plan_id"])
        self.assertEqual(second["plan"]["parallel_paths"], 1)
        self.change("create", "VCC", ["A3", "A19"], 20)
        _, done = self.change("modify", "VCC", ["A3", "A19", "B2"])
        self.assertNotIn("parallel", done["netlist"]["nets"][0])

    def test_declared_currents_are_limited_in_total(self):
        self.change("create", "VCC", ["A3", "A19"], 60)
        refused = self.session.plan("create", "GND", ["A2", "A16"], 60)
        self.assertEqual(refused["error"]["code"], "CURRENT_TOO_HIGH")
        self.assertEqual(self.session.plan("delete", "VCC", None, 10)["error"]["code"], "BAD_ARG")
        self.assertEqual(self.session.plan("create", "X", ["A4", "A5"], 0)["error"]["code"], "BAD_ARG")

    def test_measure_voltage_tool(self):
        self.change("create", "PULL", ["A18", "TEST_SRC"])
        reading = self.ok(self.session.measure("A18"))
        self.assertEqual((reading["volts"], reading["net"], reading["path"]), (3.3, "PULL", "net"))
        self.assertEqual(self.device.meas_log[-1], (FLIP["A18"], 0))
        self.device.clip_mv[FLIP["B4"]] = 4980
        free = self.ok(self.session.measure("B4", 250))
        self.assertEqual((free["volts"], free["net"], free["path"], free["window_ms"]), (4.98, None, "temporary", 250))
        self.assertIn("hint", self.ok(self.session.measure("B5")))  # open clip reads 2.5 V
        for node, window, code in (("TEST_SRC", 0, "NODE_NOT_MEASURABLE"), ("Z9", 0, "NODE_NOT_IN_TOPOLOGY"),
                                   ("A1", 2000, "BAD_ARG")):
            self.assertEqual(self.session.measure(node, window)["error"]["code"], code)
        self.assertEqual(self.device.epoch, 1)  # only the PULL change touched the switches

    def test_measure_on_v01_is_unsupported(self):
        device = MockDevice(clock=lambda: 0, geometry=V01)
        session = AgentSession(mock_opener("v01", device), heartbeat_s=None)
        self.addCleanup(session.close)
        self.ok(session.describe_board())
        clip = next(name for name, node in session.profile.nodes.items() if node["kind"] == "clip")
        self.assertEqual(session.measure(clip)["error"]["code"], "UNSUPPORTED")


if __name__ == "__main__":
    unittest.main()
