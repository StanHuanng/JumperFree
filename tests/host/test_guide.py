from __future__ import annotations

import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jumperfree.cli import main
from jumperfree.host import GUIDE_PALETTE, HostError, MockTransport, plan_guide
from jumperfree.routing.crossbar import small_topology
from jumperfree.routing.steiner import crossbar_create_net, crossbar_init_state
from jumperfree.routing.v01_topology import v01_topology


def example_state() -> dict:
    state = crossbar_init_state(v01_topology())
    state = crossbar_create_net(state, "zeta", ["Clip0", "Clip15"])
    return crossbar_create_net(state, "alpha", ["Clip2", "Clip10", "Clip8"])


EXAMPLE_PLAN = [
    (0, 0, 255, 0), (2, 255, 0, 0), (8, 255, 0, 0),
    (10, 255, 0, 0), (15, 0, 255, 0),
]
EXAMPLE_FRAMES = [
    b"JF1 CLRALL\n", b"JF1 SET 0 0 255 0\n", b"JF1 SET 2 255 0 0\n",
    b"JF1 SET 8 255 0 0\n", b"JF1 SET 10 255 0 0\n", b"JF1 SET 15 0 255 0\n",
]


class GuidePlanTests(unittest.TestCase):
    def test_empty_state_has_no_lit_clips(self) -> None:
        self.assertEqual(plan_guide(crossbar_init_state(v01_topology())), [])

    def test_deterministic_plan_and_no_mutation(self) -> None:
        state = example_state()
        original = copy.deepcopy(state)
        self.assertEqual(plan_guide(state), EXAMPLE_PLAN)
        self.assertEqual(plan_guide(state), EXAMPLE_PLAN)
        self.assertEqual(state, original)
        state["nets"] = dict(reversed(list(state["nets"].items())))
        self.assertEqual(plan_guide(json.loads(json.dumps(state))), EXAMPLE_PLAN)
        # Creation history and route resources do not affect the color plan.
        recreated = crossbar_init_state(v01_topology())
        for name in ("alpha", "zeta"):
            recreated = crossbar_create_net(recreated, name, state["nets"][name]["nodes"])
        self.assertEqual(plan_guide(recreated), EXAMPLE_PLAN)

    def test_eight_nets_have_distinct_nonblack_fixed_colors(self) -> None:
        expected_colors = (
            (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0),
            (255, 0, 255), (0, 255, 255), (255, 128, 0), (128, 0, 255),
        )
        self.assertEqual(GUIDE_PALETTE, expected_colors)
        state = crossbar_init_state(v01_topology())
        for index in reversed(range(8)):
            state = crossbar_create_net(
                state, f"net{index}", [f"Clip{index}", f"Clip{index + 8}"],
            )
        plan = plan_guide(state)
        self.assertEqual(plan, [
            (clip, *expected_colors[clip % 8]) for clip in range(16)
        ])
        self.assertEqual(len({entry[1:] for entry in plan}), 8)
        self.assertNotIn((0, 0, 0), {entry[1:] for entry in plan})

    def test_rejects_unsupported_nodes_even_without_nets(self) -> None:
        for node in ("N0", "Clip16", "Clip-1", "Clip00", "clip0", "Clip１"):
            with self.subTest(node=node):
                topology = v01_topology()
                topology["node_attachments"][node] = topology["node_attachments"].pop("Clip0")
                with self.assertRaises(HostError) as raised:
                    plan_guide(crossbar_init_state(topology))
                self.assertEqual(raised.exception.as_dict(), {
                    "code": "UNSUPPORTED_TOPOLOGY",
                    "message": "guide requires Clip0..Clip15 node IDs",
                    "details": {"nodes": [node]},
                })

    def test_malformed_states_are_host_errors(self) -> None:
        invalid_version = crossbar_init_state(v01_topology())
        invalid_version["version"] = 999
        invalid_net = example_state()
        invalid_net["nets"]["alpha"]["nodes"] = ["Clip16", "Clip2"]
        overlap = example_state()
        overlap["nets"]["zeta"]["nodes"] = ["Clip0", "Clip2"]
        invalid_topology = crossbar_init_state(v01_topology())
        invalid_topology["topology"] = {}
        for state in (None, [], {}, invalid_version, invalid_net, overlap, invalid_topology):
            with self.subTest(state=state), self.assertRaises(HostError) as raised:
                plan_guide(state)
            self.assertEqual(raised.exception.code, "INVALID_STATE")


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
        with mock.patch("jumperfree.host.cli.MockTransport", return_value=transport):
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
        with mock.patch("jumperfree.host.cli.MockTransport", return_value=transport):
            code, payload, _ = self.invoke("--mock")
        self.assertEqual(code, 0)
        self.assertEqual(payload["data"], {"plan": []})
        self.assertEqual(transport.writes, [b"JF1 CLRALL\n"])
        self.assertEqual(transport.leds, [(0, 0, 0)] * 16)
        self.assertTrue(transport.closed)

    def test_serial_guide_uses_same_frames(self) -> None:
        transport = MockTransport()
        with mock.patch("jumperfree.host.cli.SerialTransport", return_value=transport) as serial:
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
                        mock.patch("jumperfree.host.cli.MockTransport") as simulated,
                        mock.patch("jumperfree.host.cli.SerialTransport") as serial,
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
                with mock.patch("jumperfree.host.cli.MockTransport", return_value=transport):
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
