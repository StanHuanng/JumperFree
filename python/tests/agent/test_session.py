"""Agent session over an in-memory JumperFree mini: order gating, lease, heartbeat, exit."""

import unittest

from jumperfree.agent import AgentSession, mock_opener, open_session
from jumperfree.agent.ports import open_board
from jumperfree.core.geometry import V01, V04
from jumperfree.host import HostError
from jumperfree.host.mock_device import MockDevice


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def session_on(device=None, board="mini", **kwargs):
    device = device or MockDevice(clock=lambda: 0, geometry=V04 if board == "mini" else V01)
    kwargs.setdefault("heartbeat_s", None)
    return AgentSession(mock_opener(board, device), **kwargs), device


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.session, self.device = session_on()
        self.addCleanup(self.session.close)

    def ok(self, result):
        self.assertTrue(result["ok"], result.get("error"))
        self.assertIs(result["verified"], False)
        return result

    def code(self, result):
        self.assertFalse(result["ok"])
        return result["error"]["code"]

    def run_change(self, action, name, nodes=None):
        plan = self.ok(self.session.plan(action, name, nodes))
        self.ok(self.session.guide(plan["plan_id"]))
        return self.ok(self.session.execute(plan["plan_id"]))

    def test_describe_lists_mini_nodes_rows_and_services(self):
        board = self.ok(self.session.describe_board())
        self.assertEqual(board["board"]["name"], "JumperFree mini")
        names = [clip["node"] for clip in board["clips"]]
        self.assertEqual(names, [f"A{c}" for c in range(1, 25)] + [f"B{c}" for c in range(1, 25)])
        self.assertEqual({s["node"] for s in board["service_nodes"]}, {"TEST_SRC", "SVC_GND"})
        self.assertNotIn("~U307.Y0", str(board))
        self.assertEqual(self.device.lease_ms, 5000)
        self.assertEqual(self.device.state, "ARMED")

    def test_plan_is_read_only_and_execute_needs_guided_plan(self):
        plan = self.ok(self.session.plan("create", "SDA", ["A3", "B5"]))
        self.assertRegex(plan["plan_id"], r"^p-[0-9a-f]{10}$")
        self.assertEqual(plan["plan"]["makes"], 2)
        self.assertEqual(self.device.epoch, 0)
        self.assertEqual(self.code(self.session.execute(plan["plan_id"])), "PLAN_NOT_GUIDED")
        self.assertEqual(self.device.epoch, 0)
        guide = self.ok(self.session.guide(plan["plan_id"]))
        self.assertEqual(guide["led"]["added_nodes"], ["A3", "B5"])
        self.assertEqual(self.device.epoch, 0)
        lit = {i for i, rgb in enumerate(self.device.leds) if rgb != (0, 0, 0)}
        self.assertEqual(lit, {22, 47})  # A3 = FLIP22, B5 = FLIP47
        done = self.ok(self.session.execute(plan["plan_id"]))
        self.assertEqual(done["netlist"]["nets"][0]["nodes"], ["A3", "B5"])
        self.assertEqual(self.device.epoch, 1)
        self.assertEqual(self.code(self.session.execute(plan["plan_id"])), "PLAN_UNKNOWN")

    def test_any_change_makes_older_plans_stale(self):
        first = self.ok(self.session.plan("create", "A", ["A1", "A2"]))
        second = self.ok(self.session.plan("create", "B", ["B1", "B2"]))
        self.ok(self.session.guide(first["plan_id"]))
        self.ok(self.session.guide(second["plan_id"]))
        self.ok(self.session.execute(first["plan_id"]))
        self.assertEqual(self.code(self.session.execute(second["plan_id"])), "PLAN_UNKNOWN")
        self.assertEqual(self.code(self.session.guide("p-0000000000")), "PLAN_UNKNOWN")

    def test_modify_delete_and_colors_stay_with_their_net(self):
        self.run_change("create", "SCL", ["A1", "B1"])
        color_scl = self.device.leds[20]  # A1 = FLIP20
        self.run_change("create", "SDA", ["A2", "B2"])
        self.assertEqual(self.device.leds[20], color_scl)
        state = self.run_change("modify", "SDA", ["A2", "B3", "SVC_GND"])
        self.assertEqual({n["net"]: n["nodes"] for n in state["netlist"]["nets"]},
                         {"SCL": ["A1", "B1"], "SDA": ["A2", "B3", "SVC_GND"]})
        self.run_change("delete", "SCL")
        self.assertEqual(self.device.leds[20], (0, 0, 0))
        self.assertEqual([n["net"] for n in self.ok(self.session.get_state())["netlist"]["nets"]], ["SDA"])

    def test_rejects_reserved_nodes_service_short_unknown_names_and_shapes(self):
        cases = [
            (("create", "X", ["A1", "~U307.Y0"]), "NODE_RESERVED"),
            (("create", "X", ["A1", "TEST_SRC", "SVC_GND"]), "SERVICE_SHORT"),
            (("create", "X", ["A1", "U1"]), "NODE_NOT_IN_TOPOLOGY"),
            (("create", "X", ["A1"]), "NET_TOO_SMALL"),
            (("rename", "X", ["A1", "A2"]), "BAD_ARG"),
            (("create", " ", ["A1", "A2"]), "INVALID_NET_ID"),
            (("create", "X", "A1"), "BAD_ARG"),
            (("delete", "nope", None), "NET_NOT_FOUND"),
        ]
        for args, code in cases:
            with self.subTest(args=args):
                self.assertEqual(self.code(self.session.plan(*args)), code)
        self.assertEqual(self.device.epoch, 0)

    def test_fifteen_nets_fit_the_sixteenth_is_refused(self):
        for index in range(15):
            self.run_change("create", f"N{index:02}", [f"A{index + 1}", f"B{index + 1}"])
        self.assertEqual(self.code(self.session.plan("create", "N15", ["A16", "B16"])), "PLAN_TOO_LARGE")

    def test_all_open_clears_switches_and_leds_and_session_continues(self):
        self.run_change("create", "SDA", ["A3", "B5"])
        result = self.ok(self.session.all_open())
        self.assertEqual(result["netlist"]["nets"], [])
        self.assertFalse(any(self.device.shadow.closed))
        self.assertEqual(set(self.device.leds), {(0, 0, 0)})
        self.assertEqual(self.device.state, "ARMED")
        self.run_change("create", "SDA", ["A3", "B5"])

    def test_close_clears_leds_then_opens_all_and_is_idempotent(self):
        self.run_change("create", "SDA", ["A3", "B5"])
        self.session.close()
        self.assertFalse(any(self.device.shadow.closed))
        self.assertEqual(set(self.device.leds), {(0, 0, 0)})
        self.assertEqual(self.device.state, "SAFE")
        self.assertTrue(self.device.closed)
        self.session.close()
        self.assertEqual(self.code(self.session.get_state()), "SESSION_CLOSED")


class StartupTests(unittest.TestCase):
    def test_stale_switches_and_latched_lease_fault_are_cleared_at_start(self):
        device = MockDevice(clock=lambda: 0, geometry=V04)
        device.write(b"JF2 HELLO 2 1\n")
        device.write(b"JF2 TXN BEGIN 1 0 1\n")
        device.write(b"JF2 TXN ADD 1 0 +0:0:0@1\n")
        from jumperfree.host.jf2 import commit_crc
        device.write(f"JF2 TXN COMMIT 1 1 {commit_crc(['+0:0:0@1'], V04):08x}\n".encode())
        device.faults |= 0x1  # a previous Host died and its lease expired
        session, _ = session_on(device)
        self.addCleanup(session.close)
        board = session.describe_board()
        self.assertTrue(board["ok"], board.get("error"))
        self.assertEqual(board["startup_notes"],
                         ["cleared latched faults 0x1", "opened switches left by an earlier program"])
        self.assertFalse(any(device.shadow.closed))

    def test_board_mismatch_and_lazy_connect_retry(self):
        device = MockDevice(clock=lambda: 0, geometry=V01)
        session = AgentSession(mock_opener("v01", device), board="mini", heartbeat_s=None)
        self.addCleanup(session.close)
        self.assertEqual(session.describe_board()["error"]["code"], "BOARD_MISMATCH")
        calls = []

        def opener():
            calls.append(1)
            raise HostError("DEVICE_NOT_FOUND", "no board")
        lazy = AgentSession(opener, heartbeat_s=None)
        self.assertEqual(lazy.plan("create", "A", ["A1", "A2"])["error"]["code"], "DEVICE_NOT_FOUND")
        self.assertEqual(lazy.get_state()["error"]["code"], "DEVICE_NOT_FOUND")
        self.assertEqual(len(calls), 2)  # not connected yet: every call tries again
        lazy.close()

    def test_v01_board_still_works_through_the_same_tools(self):
        session, device = session_on(board="v01")
        self.addCleanup(session.close)
        plan = session.plan("create", "N", ["Clip0", "Clip9"])
        self.assertTrue(session.guide(plan["plan_id"])["ok"])
        self.assertTrue(session.execute(plan["plan_id"])["ok"])
        self.assertEqual(device.epoch, 1)


class LeaseTests(unittest.TestCase):
    def test_heartbeat_detects_lease_expiry_reboot_and_unreachable(self):
        for scenario in ("lease", "reboot", "gone"):
            with self.subTest(scenario=scenario):
                clock = Clock()
                device = MockDevice(clock=clock, geometry=V04)
                session, _ = session_on(device)
                plan = session.plan("create", "SDA", ["A3", "B5"])
                session.guide(plan["plan_id"])
                session.execute(plan["plan_id"])
                session.beat()
                clock.now += 1000
                session.beat()
                self.assertIsNone(session._lost)
                if scenario == "lease":
                    clock.now += 6000  # Host stalled longer than the lease
                elif scenario == "reboot":
                    clock.now += 1000
                    device.reboot()  # reset button or emergency stop
                else:
                    device.closed = True
                session.beat()
                expected = {"lease": "DEVICE_FAULT", "reboot": "DEVICE_REBOOTED",
                            "gone": "DEVICE_UNREACHABLE"}[scenario]
                self.assertEqual(session._lost.code, expected)
                if scenario != "gone":
                    self.assertFalse(any(device.shadow.closed))
                for result in (session.get_state(), session.plan("create", "X", ["A1", "A2"]),
                               session.guide(), session.all_open()):
                    self.assertEqual(result["error"]["code"], "SESSION_LOST")
                epoch = device.epoch
                session.close()  # never re-arms or sends ALLOPEN on a lost session
                self.assertEqual(device.epoch, epoch)

    def test_lease_keeps_switches_while_heartbeat_runs(self):
        clock = Clock()
        device = MockDevice(clock=clock, geometry=V04)
        session, _ = session_on(device)
        self.addCleanup(session.close)
        plan = session.plan("create", "SDA", ["A3", "B5"])
        session.guide(plan["plan_id"])
        session.execute(plan["plan_id"])
        for _ in range(30):
            clock.now += 1000
            session.beat()
        self.assertIsNone(session._lost)
        self.assertTrue(any(device.shadow.closed))

    def test_real_heartbeat_thread_pings_under_the_lock(self):
        device = MockDevice(geometry=V04)
        session = AgentSession(mock_opener("mini", device), heartbeat_s=0.02)
        self.addCleanup(session.close)
        self.assertTrue(session.describe_board()["ok"])
        import time
        time.sleep(0.15)
        self.assertGreater(session._ping_seq, 2)
        self.assertIsNone(session._lost)


class PortTests(unittest.TestCase):
    def test_auto_search_skips_busy_and_foreign_ports(self):
        opened = {}

        def factory(name):
            if name == "COM5":
                raise HostError("PORT_BUSY", "busy")
            device = MockDevice(clock=lambda: 0, geometry=V04)
            if name == "COM6":
                device.write = lambda data: len(data)  # silent foreign device
            opened[name] = device
            return device
        transport, name, info = open_board(None, serial_factory=factory, ports=lambda: ["COM5", "COM6", "COM7"])
        self.assertEqual((name, info["board"]), ("COM7", "v04"))
        self.assertTrue(opened["COM6"].closed)
        with self.assertRaises(HostError) as raised:
            open_board(None, serial_factory=factory, ports=lambda: ["COM5"])
        self.assertEqual(raised.exception.code, "PORT_BUSY")
        with self.assertRaises(HostError) as raised:
            open_board(None, serial_factory=factory, ports=lambda: [])
        self.assertEqual(raised.exception.code, "DEVICE_NOT_FOUND")
        with self.assertRaises(HostError) as raised:
            open_board("COM5", serial_factory=factory)
        self.assertEqual(raised.exception.code, "PORT_BUSY")

    def test_open_session_modes(self):
        self.assertEqual(open_session("mock", heartbeat_s=None).describe_board()["board"]["profile"], "mini")
        with self.assertRaises(HostError):
            open_session("tcp")


if __name__ == "__main__":
    unittest.main()
