from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jumperfree.cli import main
from jumperfree.board import LedPlanError
from jumperfree.host import HostError, MockTransport
from jumperfree.routing.crossbar import small_topology
from jumperfree.routing.steiner import crossbar_init_state
from jumperfree.routing.v01_topology import v01_topology
from tests.guide_fixtures import EXAMPLE_FRAMES, EXAMPLE_PLAN, example_state


class GuideCliTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "state.json"
        self.path.write_text(json.dumps(example_state()), encoding="utf-8")

    def invoke(self, *connection: str) -> tuple[int, dict, str]:
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = main(["host", "guide", "--state", str(self.path), *connection])
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        payload = json.loads(lines[1].removeprefix("HOST_JSON "))
        self.assertEqual(
            lines[1], "HOST_JSON " + json.dumps(payload, sort_keys=True, separators=(",", ":")),
        )
        return code, payload, lines[0]

    def test_exact_frames_clear_stale_colors_and_preserve_state_file(self) -> None:
        original = self.path.read_bytes()
        transport = MockTransport()
        transport.leds = [(1, 2, 3)] * 16
        with mock.patch("jumperfree.service.guide.MockTransport", return_value=transport):
            code, payload, summary = self.invoke("--mock")
        self.assertEqual(code, 0)
        self.assertEqual(summary, "HOST guide OK")
        self.assertEqual(payload, {
            "data": {"plan": [list(entry) for entry in EXAMPLE_PLAN]},
            "ok": True, "operation": "guide",
        })
        self.assertEqual(transport.writes, EXAMPLE_FRAMES)
        expected_leds = [(0, 0, 0)] * 16
        for clip, r, g, b in EXAMPLE_PLAN:
            expected_leds[clip] = (r, g, b)
        self.assertEqual(transport.leds, expected_leds)
        self.assertTrue(transport.closed)
        self.assertEqual(self.path.read_bytes(), original)

    def test_empty_state_sends_only_clear_all(self) -> None:
        self.path.write_text(json.dumps(crossbar_init_state(v01_topology())), encoding="utf-8")
        transport = MockTransport()
        transport.leds = [(255, 255, 255)] * 16
        with mock.patch("jumperfree.service.guide.MockTransport", return_value=transport):
            code, payload, _ = self.invoke("--mock")
        self.assertEqual(code, 0)
        self.assertEqual(payload["data"], {"plan": []})
        self.assertEqual(transport.writes, [b"JF1 CLRALL\n"])
        self.assertEqual(transport.leds, [(0, 0, 0)] * 16)
        self.assertTrue(transport.closed)

    def test_serial_guide_uses_same_frames(self) -> None:
        transport = MockTransport()
        with mock.patch("jumperfree.service.guide.SerialTransport", return_value=transport) as serial:
            code, payload, _ = self.invoke("--port", "/dev/test")
        serial.assert_called_once_with("/dev/test", 115200, 1.0)
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(transport.writes, EXAMPLE_FRAMES)
        self.assertTrue(transport.closed)

    def test_invalid_files_and_topologies_do_not_open_transport(self) -> None:
        for contents, expected in (
            (None, "STATE_NOT_FOUND"), (b"{", "INVALID_STATE"),
            (b"\xff", "INVALID_STATE"), (b"[]", "INVALID_STATE"),
            (b"{}", "INVALID_STATE"),
            (json.dumps(crossbar_init_state(small_topology())).encode(), "UNSUPPORTED_TOPOLOGY"),
        ):
            for connection in (("--mock",), ("--port", "/dev/test")):
                with self.subTest(contents=contents, connection=connection):
                    self.path.unlink(missing_ok=True)
                    if contents is not None:
                        self.path.write_bytes(contents)
                    with (
                        mock.patch("jumperfree.service.guide.MockTransport") as simulated,
                        mock.patch("jumperfree.service.guide.SerialTransport") as serial,
                    ):
                        code, payload, summary = self.invoke(*connection)
                    simulated.assert_not_called()
                    serial.assert_not_called()
                    self.assertEqual(code, 1)
                    self.assertFalse(payload["ok"])
                    self.assertEqual(payload["operation"], "guide")
                    self.assertEqual(payload["error"]["code"], expected)
                    self.assertTrue(summary.startswith(f"HOST FAIL {expected}:"))

    def test_unreadable_state_is_structured(self) -> None:
        with mock.patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            code, payload, _ = self.invoke("--mock")
        self.assertEqual(code, 1)
        self.assertEqual(payload["error"]["code"], "INVALID_STATE")

    def test_failure_stops_frames_and_closes_transport(self) -> None:
        for responses, expected_frames in (
            ([], EXAMPLE_FRAMES[:1]),
            ([b"JF1 OK CLRALL\n"], EXAMPLE_FRAMES[:2]),
            ([b"JF1 OK CLRALL\n", b"JF1 OK SET 0 0 255 0\n"], EXAMPLE_FRAMES[:3]),
        ):
            with self.subTest(responses=responses):
                transport = MockTransport(responses)
                with mock.patch("jumperfree.service.guide.MockTransport", return_value=transport):
                    code, payload, _ = self.invoke("--mock")
                self.assertEqual(code, 1)
                self.assertEqual(payload["error"]["code"], "TIMEOUT")
                self.assertEqual(transport.writes, expected_frames)
                self.assertTrue(transport.closed)

    def test_mock_rejects_serial_options_before_loading_state(self) -> None:
        self.path.unlink()
        for options in (("--baudrate", "115200"), ("--timeout", "1")):
            with self.subTest(options=options):
                code, payload, _ = self.invoke("--mock", *options)
                self.assertEqual(code, 1)
                self.assertEqual(payload["error"]["code"], "INVALID_SERIAL_CONFIG")

    def test_parser_requires_state_and_connection(self) -> None:
        for options in (
            [], ["--mock"], ["--state", str(self.path)],
            ["--state", str(self.path), "--mock", "--port", "/dev/test"],
        ):
            with self.subTest(options=options), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    main(["host", "guide", *options])
                self.assertEqual(raised.exception.code, 2)

    def test_close_failure_after_success_is_reported(self) -> None:
        transport = MockTransport()
        with (
            mock.patch("jumperfree.service.guide.MockTransport", return_value=transport),
            mock.patch.object(transport, "close", side_effect=OSError("close failed")) as close,
        ):
            code, payload, summary = self.invoke("--mock")
        close.assert_called_once_with()
        self.assertEqual(transport.writes, EXAMPLE_FRAMES)
        self.assertEqual(code, 1)
        self.assertEqual(summary, "HOST FAIL TRANSPORT_ERROR: close failed")
        self.assertEqual(payload, {
            "ok": False, "operation": "guide",
            "error": {"code": "TRANSPORT_ERROR", "message": "close failed",
                      "details": {"operation": "close"}},
        })

    def test_close_failure_preserves_primary_error(self) -> None:
        for replies, frames, error in (
            ([], EXAMPLE_FRAMES[:1],
             {"code": "TIMEOUT", "message": "no response before transport timeout"}),
            ([b"JF1 OK CLRALL\n", b"JF1 ERR BUSY try later\n"], EXAMPLE_FRAMES[:2],
             {"code": "DEVICE_ERROR", "message": "try later", "details": {"device_code": "BUSY"}}),
        ):
            with self.subTest(replies=replies):
                transport = MockTransport(replies)
                with (
                    mock.patch("jumperfree.service.guide.MockTransport", return_value=transport),
                    mock.patch.object(transport, "close", side_effect=OSError("close failed")) as close,
                ):
                    code, payload, summary = self.invoke("--mock")
                close.assert_called_once_with()
                self.assertEqual(transport.writes, frames)
                self.assertEqual(code, 1)
                self.assertEqual(summary, f"HOST FAIL {error['code']}: {error['message']}")
                self.assertEqual(payload["error"], {
                    **error, "details": {
                        **error.get("details", {}),
                        "close_error": {"code": "TRANSPORT_ERROR", "message": "close failed",
                                        "details": {"operation": "close"}},
                    },
                })

    def test_board_error_details_are_converted_before_opening_transport(self) -> None:
        with (
            mock.patch("jumperfree.service.guide.plan_guide", side_effect=LedPlanError(
                "INVALID_STATE", "invalid board state", net="a",
            )),
            mock.patch("jumperfree.service.guide.MockTransport") as transport,
        ):
            code, payload, summary = self.invoke("--mock")
        transport.assert_not_called()
        self.assertEqual(code, 1)
        self.assertEqual(summary, "HOST FAIL INVALID_STATE: invalid board state")
        self.assertEqual(payload["error"], {
            "code": "INVALID_STATE", "message": "invalid board state", "details": {"net": "a"},
        })

    def test_serial_options_and_open_errors_preserve_host_output(self) -> None:
        transport = MockTransport()
        with mock.patch("jumperfree.service.guide.SerialTransport", return_value=transport) as serial:
            code, payload, _ = self.invoke("--port", "/dev/test", "--baudrate", "9600", "--timeout", "0.5")
        serial.assert_called_once_with("/dev/test", 9600, 0.5)
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertTrue(transport.closed)
        with mock.patch("jumperfree.service.guide.SerialTransport", side_effect=HostError(
            "TRANSPORT_ERROR", "open failed", operation="open",
        )):
            code, payload, _ = self.invoke("--port", "/dev/test")
        self.assertEqual(code, 1)
        self.assertEqual(payload["error"], {
            "code": "TRANSPORT_ERROR", "message": "open failed", "details": {"operation": "open"},
        })
