"""stdio MCP protocol over a mock JumperFree mini (software evidence only)."""

import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from jumperfree.agent import INSTRUCTIONS, TOOL_SCHEMAS, AgentSession, mock_opener
from jumperfree.agent.mcp_server import McpServer, serve
from jumperfree.core.geometry import MINI
from jumperfree.host.mock_device import MockDevice

SRC = Path(__file__).resolve().parents[2] / "src"


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.device = MockDevice(clock=lambda: 0, geometry=MINI)
        self.session = AgentSession(mock_opener("mini", self.device), heartbeat_s=None)
        self.addCleanup(self.session.close)
        self.server = McpServer(self.session)
        self._id = 0

    def call(self, tool, /, **arguments):
        self._id += 1
        response = self.server.handle({"jsonrpc": "2.0", "id": self._id, "method": "tools/call",
                                       "params": {"name": tool, "arguments": arguments}})
        result = response["result"]
        self.assertEqual(json.loads(result["content"][0]["text"]), result["structuredContent"])
        self.assertEqual(result["isError"], not result["structuredContent"]["ok"])
        self.assertIs(result["structuredContent"]["verified"], False)
        return result["structuredContent"]

    def test_initialize_negotiates_version_and_carries_instructions(self):
        for asked, expected in (("2024-11-05", "2024-11-05"), ("2025-06-18", "2025-06-18"),
                                ("1999-01-01", "2025-06-18"), (None, "2025-06-18")):
            init = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                       "params": {"protocolVersion": asked} if asked else {}})
            self.assertEqual(init["result"]["protocolVersion"], expected)
            self.assertEqual(init["result"]["instructions"], INSTRUCTIONS)
        self.assertIsNone(self.server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        listed = self.server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual([t["name"] for t in listed["result"]["tools"]], [
            "describe_board", "get_state", "plan_net_change", "guide_leds", "execute_plan", "all_open",
            "measure_voltage"])
        self.assertEqual(self.device.epoch, 0)  # listing never touches the board

    def test_no_tool_exposes_crosspoints_or_registers(self):
        for schema in TOOL_SCHEMAS.values():
            props = set(schema["inputSchema"]["properties"])
            # Net intent, plan ids, a load current and a node to measure: never a BUS or crosspoint.
            self.assertLessEqual(props, {"action", "name", "nodes", "plan_id", "current_ma", "node", "window_ms"})
            self.assertFalse(schema["inputSchema"]["additionalProperties"])

    def test_bad_requests_are_protocol_errors(self):
        for message, code in (
            ({"jsonrpc": "2.0", "id": 1, "method": "nope"}, -32601),
            ({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "x"}}, -32602),
            ({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": "execute_plan", "arguments": {}}}, -32602),
            ({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": "plan_net_change", "arguments": {"action": "create", "name": "A",
                                                                  "nodes": ["A1", "A2"], "chip": 0}}}, -32602),
            ([], -32600),
        ):
            with self.subTest(message=message):
                self.assertEqual(self.server.handle(message)["error"]["code"], code)

    def test_full_change_through_tools(self):
        plan = self.call("plan_net_change", action="create", name="SDA", nodes=["A3", "B5"])
        self.assertEqual(self.call("execute_plan", plan_id=plan["plan_id"])["error"]["code"], "PLAN_NOT_GUIDED")
        self.assertTrue(self.call("guide_leds", plan_id=plan["plan_id"])["ok"])
        self.assertTrue(self.call("execute_plan", plan_id=plan["plan_id"])["ok"])
        self.assertEqual(self.call("get_state")["netlist"]["nets"][0]["nodes"], ["A3", "B5"])

    def test_serve_loop_handles_parse_error_and_eof(self):
        out = io.StringIO()
        serve(self.session, io.StringIO('not json\n{"jsonrpc":"2.0","id":1,"method":"ping"}\n'), out)
        lines = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(lines[0]["error"]["code"], -32700)
        self.assertEqual(lines[1]["result"], {})

    def test_stdio_subprocess_mock_exits_with_all_open_log(self):
        requests = "\n".join(json.dumps(m) for m in (
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "plan_net_change",
                        "arguments": {"action": "create", "name": "温度", "nodes": ["A1", "B1"]}}},
        )) + "\n"
        env = dict(os.environ, PYTHONPATH=str(SRC))
        done = subprocess.run([sys.executable, "-m", "jumperfree.agent.mcp_server", "--mock"],
                              input=requests.encode("utf-8"), capture_output=True, env=env, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        lines = [json.loads(line) for line in done.stdout.decode("utf-8").splitlines()]
        self.assertEqual(lines[1]["result"]["structuredContent"]["plan"]["net"], "温度")
        self.assertIn("SW ALLOPEN", done.stderr.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
