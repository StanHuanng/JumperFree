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

    def test_input_errors_are_structured_before_opening_serial(self) -> None:
        cases = (
            (("set", "--clip", "0", "--rgb", "1,2"), "INVALID_RGB"),
            (("set", "--clip", "0", "--rgb", "1,2,3,4"), "INVALID_RGB"),
            (("set", "--clip", "0", "--rgb", "1,no,3"), "INVALID_RGB"),
            (("set", "--clip", "0", "--rgb", "1,,3"), "INVALID_RGB"),
            (("set", "--clip", "0", "--rgb", "256,0,0"), "INVALID_RGB"),
            (("set", "--clip", "0", "--rgb", "0,-1,0"), "INVALID_RGB"),
            (("set", "--clip", "0", "--rgb", "0,0,256"), "INVALID_RGB"),
            (("set", "--clip", "-1", "--rgb", "1,2,3"), "CLIP_OUT_OF_RANGE"),
            (("set", "--clip", "16", "--rgb", "1,2,3"), "CLIP_OUT_OF_RANGE"),
            (("clear", "--clip", "-1"), "CLIP_OUT_OF_RANGE"),
            (("clear", "--clip", "16"), "CLIP_OUT_OF_RANGE"),
        )
        for args, expected in cases:
            for connection in (("--mock",), ("--port", "/dev/test")):
                with self.subTest(args=args, connection=connection), mock.patch(
                    "jumperfree.host.cli.SerialTransport",
                    side_effect=HostError("SERIAL_UNAVAILABLE", "serial unavailable"),
                ) as serial:
                    code, payload, summary = self.run_cli(*args, *connection)
                    serial.assert_not_called()
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

    def test_serial_defaults_without_pyserial(self) -> None:
        with mock.patch(
            "jumperfree.host.cli.SerialTransport", return_value=MockTransport(),
        ) as serial:
            code, payload, _ = self.run_cli("status", "--port", "/dev/test")
        serial.assert_called_once_with("/dev/test", 115200, 1.0)
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])

    def test_mock_rejects_explicit_serial_options_before_creating_transport(self) -> None:
        for command in (
            ("set", "--clip", "0", "--rgb", "1,2,3"),
            ("clear", "--clip", "0"), ("clear-all",), ("status",),
        ):
            for options in (
                ("--baudrate", "115200"), ("--baudrate", "0"), ("--baudrate", "-1"),
                ("--timeout", "1"), ("--timeout", "0"), ("--timeout", "-1"),
                ("--timeout", "nan"), ("--timeout", "inf"), ("--timeout", "1e1000"),
                ("--baudrate", "9600", "--timeout", "0.5"),
            ):
                with (
                    self.subTest(command=command, options=options),
                    mock.patch("jumperfree.host.cli.MockTransport") as mock_transport,
                    mock.patch("jumperfree.host.cli.SerialTransport") as serial,
                ):
                    code, payload, summary = self.run_cli(*command, "--mock", *options)
                mock_transport.assert_not_called()
                serial.assert_not_called()
                self.assertEqual(code, 1)
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["error"]["code"], "INVALID_SERIAL_CONFIG")
                self.assertTrue(summary.startswith("HOST FAIL INVALID_SERIAL_CONFIG:"))

    def test_invalid_serial_options_are_not_replaced_by_defaults(self) -> None:
        for options in (
            ("--baudrate", "0"), ("--timeout", "0"),
            ("--timeout", "3600.0000000000005"), ("--timeout", "3601"),
            ("--timeout", "1e308"),
        ):
            with self.subTest(options=options), mock.patch(
                "jumperfree.host.transport.importlib.import_module",
            ) as load:
                code, payload, _ = self.run_cli("status", "--port", "/dev/test", *options)
            load.assert_not_called()
            self.assertEqual(code, 1)
            self.assertEqual(payload["error"]["code"], "INVALID_SERIAL_CONFIG")

    def test_serial_io_overflow_has_structured_output_and_closes_port(self) -> None:
        for operation in ("write", "readline"):
            with self.subTest(operation=operation):
                serial_module = mock.Mock()
                connection = serial_module.Serial.return_value
                connection.write.return_value = len(b"JF1 STATUS\n")
                getattr(connection, operation).side_effect = OverflowError(
                    "timeout exceeds platform limit",
                )
                with (
                    mock.patch(
                        "jumperfree.host.transport.importlib.import_module",
                        return_value=serial_module,
                    ),
                    contextlib.redirect_stderr(io.StringIO()) as stderr,
                ):
                    code, payload, summary = self.run_cli("status", "--port", "/dev/test")
                serial_module.Serial.assert_called_once_with(
                    port="/dev/test", baudrate=115200, timeout=1.0, write_timeout=1.0,
                )
                connection.write.assert_called_once_with(b"JF1 STATUS\n")
                if operation == "write":
                    connection.readline.assert_not_called()
                else:
                    connection.readline.assert_called_once_with(513)
                connection.close.assert_called_once_with()
                self.assertEqual(code, 1)
                self.assertEqual(stderr.getvalue(), "")
                self.assertEqual(
                    summary, "HOST FAIL INVALID_SERIAL_CONFIG: timeout exceeds platform limit",
                )
                self.assertEqual(payload, {
                    "ok": False,
                    "operation": "status",
                    "error": {
                        "code": "INVALID_SERIAL_CONFIG",
                        "message": "timeout exceeds platform limit",
                    },
                })

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

    def test_close_failure_preserves_primary_transaction_error(self) -> None:
        cases = (
            (b"", {"code": "TIMEOUT", "message": "no response before transport timeout"}),
            (b"JF1 ERR BUSY try later\n", {
                "code": "DEVICE_ERROR", "message": "try later", "details": {"device_code": "BUSY"},
            }),
        )
        for response, primary_error in cases:
            with self.subTest(response=response):
                transport = MockTransport([response])
                with (
                    mock.patch("jumperfree.host.cli.MockTransport", return_value=transport),
                    mock.patch.object(transport, "close", side_effect=OSError("close failed")) as close,
                ):
                    code, payload, summary = self.run_cli("status", "--mock")
                close.assert_called_once_with()
                self.assertEqual(transport.writes, [b"JF1 STATUS\n"])
                self.assertEqual(code, 1)
                self.assertFalse(payload["ok"])
                self.assertEqual(
                    summary, f"HOST FAIL {primary_error['code']}: {primary_error['message']}",
                )
                self.assertEqual(payload["error"], {
                    **primary_error,
                    "details": {
                        **primary_error.get("details", {}),
                        "close_error": {
                            "code": "TRANSPORT_ERROR", "message": "close failed",
                            "details": {"operation": "close"},
                        },
                    },
                })

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
