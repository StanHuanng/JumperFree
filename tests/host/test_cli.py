from __future__ import annotations

import contextlib
import io
import json
import runpy
import unittest
from unittest import mock

from jumperfree.cli import main
from jumperfree.host import HostError, MockTransport


class HostCliTests(unittest.TestCase):
    def run_cli(self, *args: str) -> tuple[int, dict[str, object], str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["host", *args])
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[1].startswith("HOST_JSON "))
        payload = json.loads(lines[1].removeprefix("HOST_JSON "))
        self.assertEqual(
            lines[1], "HOST_JSON " + json.dumps(payload, sort_keys=True, separators=(",", ":")),
        )
        return code, payload, lines[0]

    def test_all_mock_commands(self) -> None:
        for args, command, values in (
            (("set", "--clip", "15", "--rgb", "255, 0,128"), "SET", [15, 255, 0, 128]),
            (("clear", "--clip", "0"), "CLR", [0]),
            (("clear-all",), "CLRALL", []),
            (("status",), "STATUS", [0] * 48),
        ):
            with self.subTest(args=args):
                code, payload, summary = self.run_cli(*args, "--mock")
                self.assertEqual(code, 0)
                self.assertEqual(summary, f"HOST {args[0]} OK")
                self.assertEqual(payload, {
                    "data": {"command": command, "values": values},
                    "ok": True, "operation": args[0],
                })

    def test_input_errors_are_structured(self) -> None:
        cases = (
            (("set", "--clip", "0", "--rgb", "1,2"), "INVALID_RGB"),
            (("set", "--clip", "0", "--rgb", "1,2,3,4"), "INVALID_RGB"),
            (("set", "--clip", "0", "--rgb", "1,no,3"), "INVALID_RGB"),
            (("set", "--clip", "0", "--rgb", "1,,3"), "INVALID_RGB"),
            (("set", "--clip", "0", "--rgb", "256,0,0"), "INVALID_RGB"),
            (("set", "--clip", "16", "--rgb", "1,2,3"), "CLIP_OUT_OF_RANGE"),
            (("clear", "--clip", "-1"), "CLIP_OUT_OF_RANGE"),
        )
        for args, expected in cases:
            with self.subTest(args=args):
                code, payload, summary = self.run_cli(*args, "--mock")
                self.assertEqual(code, 1)
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["error"]["code"], expected)
                self.assertTrue(summary.startswith("HOST FAIL " + expected))

    def test_serial_path_and_options_without_pyserial(self) -> None:
        transport = MockTransport()
        with mock.patch("jumperfree.host.cli.SerialTransport", return_value=transport) as serial:
            code, payload, _ = self.run_cli(
                "status", "--port", "/dev/test", "--baudrate", "9600", "--timeout", "0.5",
            )
        serial.assert_called_once_with("/dev/test", 9600, 0.5)
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertTrue(transport.closed)

    def test_missing_pyserial_error(self) -> None:
        with mock.patch(
            "jumperfree.host.transport.importlib.import_module", side_effect=ImportError,
        ):
            code, payload, _ = self.run_cli("status", "--port", "/dev/test")
        self.assertEqual(code, 1)
        self.assertEqual(payload["error"]["code"], "SERIAL_UNAVAILABLE")

    def test_transaction_failure_closes_transport(self) -> None:
        transport = MockTransport([])
        with mock.patch("jumperfree.host.cli.MockTransport", return_value=transport):
            code, payload, _ = self.run_cli("status", "--mock")
        self.assertEqual(code, 1)
        self.assertEqual(payload["error"]["code"], "TIMEOUT")
        self.assertTrue(transport.closed)

    def test_close_failure_is_reported(self) -> None:
        with mock.patch(
            "jumperfree.host.cli.HostSession.close",
            side_effect=HostError("TRANSPORT_ERROR", "close failed"),
        ):
            code, payload, _ = self.run_cli("status", "--mock")
        self.assertEqual(code, 1)
        self.assertEqual(payload["error"]["code"], "TRANSPORT_ERROR")

    def test_parser_requires_explicit_connection_and_required_parameters(self) -> None:
        for args in (
            ("host",), ("host", "status"), ("host", "status", "--mock", "--port", "port"),
            ("host", "set", "--mock", "--clip", "0"),
            ("host", "clear", "--mock"), ("host", "clear", "--mock", "--clip", "bad"),
        ):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    main(args)
                self.assertEqual(raised.exception.code, 2)

    def test_module_entrypoint_runs_host(self) -> None:
        with mock.patch("sys.argv", ["jumperfree", "host", "status", "--mock"]):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                with self.assertRaises(SystemExit) as raised:
                    runpy.run_module("jumperfree", run_name="__main__")
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("HOST_JSON ", output.getvalue())
