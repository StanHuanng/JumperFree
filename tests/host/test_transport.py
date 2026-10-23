from __future__ import annotations

import unittest
from unittest import mock

from jumperfree.host import HostError, MockTransport, SerialTransport


class MockTransportTests(unittest.TestCase):
    def test_chunked_writes_and_multiple_lines(self) -> None:
        transport = MockTransport()
        self.assertEqual(transport.write(b"JF1 SET 1 2"), 11)
        self.assertEqual(transport.readline(), b"")
        transport.write(b" 3 4\r\nJF1 CLR 1\nJF1 CLRALL\nJF1 STATUS\n")
        self.assertEqual(transport.readline(), b"JF1 OK SET 1 2 3 4\n")
        self.assertEqual(transport.readline(), b"JF1 OK CLR 1\n")
        self.assertEqual(transport.readline(), b"JF1 OK CLRALL\n")
        self.assertEqual(transport.readline(), b"JF1 OK STATUS" + b" 0" * 48 + b"\n")
        self.assertEqual(transport.readline(), b"")
        transport.close()

    def test_malformed_requests_return_errors_without_mutating_leds(self) -> None:
        transport = MockTransport()
        transport.write(b"JF1 SET 1 10 20 30\n")
        transport.readline()
        for frame, code in (
            (b"JF1 CLR 16\n", b"CLIP_OUT_OF_RANGE"),
            (b"JF1 SET 1 256 0 0\n", b"INVALID_RGB"),
            (b"JF2 CLRALL\n", b"MALFORMED_FRAME"),
            (b"JF1\n", b"INVALID_COMMAND"),
            (b"JF1 CLR -1\n", b"MALFORMED_FRAME"),
        ):
            with self.subTest(frame=frame):
                transport.write(frame)
                self.assertTrue(transport.readline().startswith(b"JF1 ERR " + code + b" "))
                self.assertEqual(transport.leds, [(0, 0, 0), (10, 20, 30)] + [(0, 0, 0)] * 14)
        transport.close()

    def test_scripted_transport_and_timeout(self) -> None:
        transport = MockTransport([b"custom response\n"])
        transport.write(b"JF1 CLRALL\n")
        self.assertEqual(transport.readline(), b"custom response\n")
        self.assertEqual(transport.readline(), b"")
        transport.close()
        transport.close()
        with self.assertRaises(OSError):
            transport.write(b"JF1 STATUS\n")
        with self.assertRaises(OSError):
            transport.readline()


class SerialTransportTests(unittest.TestCase):
    def test_optional_dependency_missing(self) -> None:
        with mock.patch(
            "jumperfree.host.transport.importlib.import_module", side_effect=ImportError("no serial"),
        ):
            with self.assertRaises(HostError) as raised:
                SerialTransport("/dev/test")
        self.assertEqual(raised.exception.code, "SERIAL_UNAVAILABLE")

    def test_finite_configuration_validation_before_import(self) -> None:
        cases = (
            ("", 115200, 1), ("  ", 115200, 1), (None, 115200, 1),
            ("port", 0, 1), ("port", -1, 1), ("port", True, 1), ("port", 1.5, 1),
            ("port", 115200, 0), ("port", 115200, -1), ("port", 115200, None),
            ("port", 115200, "1"), ("port", 115200, True),
            ("port", 115200, float("inf")), ("port", 115200, float("nan")),
        )
        with mock.patch("jumperfree.host.transport.importlib.import_module") as load:
            for args in cases:
                with self.subTest(args=args):
                    with self.assertRaises(HostError) as raised:
                        SerialTransport(*args)
                    self.assertEqual(raised.exception.code, "INVALID_SERIAL_CONFIG")
            load.assert_not_called()

    def test_optional_serial_adapter_uses_bounded_reads_and_finite_timeouts(self) -> None:
        serial_module = mock.Mock()
        connection = serial_module.Serial.return_value
        connection.write.return_value = 11
        connection.readline.return_value = b"JF1 OK CLRALL\n"
        with mock.patch(
            "jumperfree.host.transport.importlib.import_module", return_value=serial_module,
        ) as load:
            transport = SerialTransport("/dev/test", 9600, 0.5)
        load.assert_called_once_with("serial")
        serial_module.Serial.assert_called_once_with(
            port="/dev/test", baudrate=9600, timeout=0.5, write_timeout=0.5,
        )
        self.assertEqual(transport.write(b"JF1 CLRALL\n"), 11)
        self.assertEqual(transport.readline(), b"JF1 OK CLRALL\n")
        connection.readline.assert_called_once_with(513)
        transport.close()
        connection.close.assert_called_once_with()

    def test_serial_open_failures(self) -> None:
        for error in (OSError("missing port"), ValueError("unsupported baudrate")):
            with self.subTest(error=error):
                serial_module = mock.Mock()
                serial_module.Serial.side_effect = error
                with mock.patch(
                    "jumperfree.host.transport.importlib.import_module", return_value=serial_module,
                ):
                    with self.assertRaises(HostError) as raised:
                        SerialTransport("/dev/test")
                self.assertEqual(raised.exception.code, "TRANSPORT_ERROR")
                self.assertEqual(raised.exception.details, {"operation": "open"})
