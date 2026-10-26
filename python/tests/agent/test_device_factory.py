"""Session factory: mock and serial modes, device label, lazy pyserial."""

import json
import sys
import unittest
from unittest import mock

from jumperfree.agent import AgentSession, MockAgentSession, get_state, create_net, open_session
from jumperfree.host import HostError
from jumperfree.host.mock_device import MockDevice


class DeviceFactoryTests(unittest.TestCase):
    def test_mock_mode_labels_mock_device(self):
        with open_session("mock") as session:
            self.assertIsInstance(session, MockAgentSession)
            result = create_net(session, "n1", ["Clip0", "Clip1"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["device"], "MockDevice")
        self.assertIs(result["verified"], False)

    def test_serial_mode_uses_injected_transport_and_label(self):
        seen = []

        def factory(port, **kwargs):
            seen.append((port, kwargs))
            return MockDevice()

        with open_session("serial", "COM7", baudrate=9600, timeout=2.0,
                          serial_factory=factory) as session:
            self.assertIsInstance(session, AgentSession)
            created = create_net(session, "n1", ["Clip0", "Clip1"])
            state = get_state(session)
        self.assertEqual(seen, [("COM7", {"baudrate": 9600, "timeout": 2.0})])
        for result in (created, state):
            self.assertTrue(result["ok"])
            self.assertEqual(result["device"], "serial:COM7")
            self.assertIs(result["verified"], False)
            self.assertEqual(json.loads(json.dumps(result)), result)

    def test_serial_service_result_objects_carry_label(self):
        with open_session("serial", "COM7", serial_factory=lambda port, **kw: MockDevice()) as session:
            ok = session._control.create("n1", ["Clip0", "Clip1"])
            failed = session._control.create("n1", ["Clip2", "Clip3"])  # duplicate
            query = session._control.query()
            planned = session._control.plan("delete", "n1")
        for result in (ok, failed, query, planned):
            self.assertEqual(result.device, "serial:COM7")
            self.assertEqual(result.as_dict()["device"], "serial:COM7")
            self.assertIs(result.verified, False)
        self.assertTrue(ok.ok)
        self.assertFalse(failed.ok)

    def test_mock_service_default_device(self):
        with open_session("mock") as session:
            self.assertEqual(session._control.query().device, "MockDevice")

    def test_closed_serial_session_keeps_label(self):
        session = open_session("serial", "/dev/ttyUSB0", serial_factory=lambda port, **kw: MockDevice())
        session.close()
        result = get_state(session)
        self.assertEqual(result["error"]["code"], "SESSION_CLOSED")
        self.assertEqual(result["device"], "serial:/dev/ttyUSB0")
        self.assertIs(result["verified"], False)

    def test_serial_connect_failure_closes_transport(self):
        dev = MockDevice()
        with mock.patch.object(dev, "readline", return_value=b""):
            with self.assertRaises(HostError):
                open_session("serial", "COM1", serial_factory=lambda port, **kw: dev)
        self.assertTrue(dev.closed)

    def test_invalid_arguments(self):
        for kwargs, code in (
            ({"mode": "serial"}, "INVALID_SERIAL_CONFIG"),
            ({"mode": "serial", "port": " "}, "INVALID_SERIAL_CONFIG"),
            ({"mode": "mock", "port": "COM1"}, "INVALID_SERIAL_CONFIG"),
            ({"mode": "usb"}, "BAD_ARG"),
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(HostError) as ctx:
                open_session(**kwargs)
            self.assertEqual(ctx.exception.code, code)

    def test_without_pyserial_mock_works_and_serial_reports_unavailable(self):
        with mock.patch.dict(sys.modules, {"serial": None}):
            with open_session("mock") as session:
                self.assertTrue(get_state(session)["ok"])
            with self.assertRaises(HostError) as ctx:
                open_session("serial", "COM1")
        self.assertEqual(ctx.exception.code, "SERIAL_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
