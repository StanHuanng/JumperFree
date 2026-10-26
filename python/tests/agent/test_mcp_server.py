"""MCP server + v1.0 scenario over MockDevice (software/Mock evidence only)."""

import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from jumperfree.agent import MockAgentSession, TOOL_SCHEMAS
from jumperfree.agent.mcp_server import McpServer, serve
from jumperfree.host import ApplyResult, HostError

SRC = Path(__file__).resolve().parents[2] / "src"


class ServerTestCase(unittest.TestCase):
    def setUp(self):
        self.session = MockAgentSession()
        self.addCleanup(self.session.close)
        self.server = McpServer(self.session)
        self._id = 0

    def call(self, tool, /, **arguments):
        self._id += 1
        response = self.server.handle({"jsonrpc": "2.0", "id": self._id, "method": "tools/call",
                                       "params": {"name": tool, "arguments": arguments}})
        self.assertEqual(response["id"], self._id)
        result = response["result"]
        self.assertEqual(json.loads(result["content"][0]["text"]), result["structuredContent"])
        self.assertEqual(result["isError"], not result["structuredContent"]["ok"])
        self.assertIs(result["structuredContent"]["verified"], False)
        return result["structuredContent"]


def nets(result):
    return {n["net"]: sorted(n["nodes"]) for n in result["netlist"]["nets"]}


class ProtocolTests(ServerTestCase):
    def test_initialize_and_list(self):
        init = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertIn("tools", init["result"]["capabilities"])
        self.assertIsNone(self.server.handle(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}))
        listed = self.server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual({t["name"] for t in listed["result"]["tools"]}, {
            "plan_net_change", "create_net", "modify_net", "delete_net",
            "get_state", "guide_leds", "all_open"})
        self.assertEqual(len(TOOL_SCHEMAS), 7)

    def test_bad_requests(self):
        for message, code in (
            ({"jsonrpc": "2.0", "id": 1, "method": "nope"}, -32601),
            ({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "x"}}, -32602),
            ({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": "create_net", "arguments": {"name": "A"}}}, -32602),
            ({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": "get_state", "arguments": {"extra": 1}}}, -32602),
            ([], -32600),
        ):
            with self.subTest(message=message):
                self.assertEqual(self.server.handle(message)["error"]["code"], code)

    def test_serve_loop_handles_parse_error_and_eof(self):
        out = io.StringIO()
        serve(self.session, io.StringIO(
            'not json\n{"jsonrpc":"2.0","id":1,"method":"ping"}\n'), out)
        lines = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(lines[0]["error"]["code"], -32700)
        self.assertEqual(lines[1]["result"], {})

    def test_stdio_subprocess(self):
        requests = "\n".join(json.dumps(m) for m in (
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "create_net", "arguments": {"name": "A", "nodes": ["Clip0", "Clip4"]}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "get_state", "arguments": {}}},
        )) + "\n"
        proc = subprocess.run([sys.executable, "-m", "jumperfree.agent.mcp_server"], input=requests,
                              capture_output=True, text=True, timeout=30,
                              env={**os.environ, "PYTHONPATH": str(SRC)})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        results = [json.loads(line)["result"]["structuredContent"] for line in proc.stdout.splitlines()]
        self.assertTrue(results[0]["ok"])
        self.assertEqual(nets(results[1]), {"A": ["Clip0", "Clip4"]})
        self.assertTrue(all(r["verified"] is False for r in results))


class ScenarioTests(ServerTestCase):
    """v1.0 scenario, 0-based: handoff nodes 1/5/9 -> Clip0/Clip4/Clip8, 3/12 -> Clip2/Clip11."""

    def test_full_scenario(self):
        dev = self.session._device
        n1, n2 = ["Clip0", "Clip4", "Clip8"], ["Clip11", "Clip2"]

        plan = self.call("plan_net_change", action="create", name="N1", nodes=n1)
        self.assertTrue(plan["ok"])
        self.assertEqual(plan["operation"], "plan")
        self.assertIsNone(plan["netlist"])
        self.assertEqual(dev.epoch, self.session._control._epoch)
        self.assertEqual(nets(self.call("get_state")), {})

        r1 = self.call("create_net", name="N1", nodes=n1)
        r2 = self.call("create_net", name="N2", nodes=n2)
        self.assertTrue(r1["ok"] and r2["ok"])
        self.assertEqual(nets(self.call("get_state")), {"N1": n1, "N2": n2})

        led = self.call("guide_leds")
        self.assertTrue(led["ok"])
        self.assertEqual(led["led"]["count"], 16)
        self.assertEqual({i for i, c in enumerate(dev.leds) if c != (0, 0, 0)}, {0, 4, 8, 2, 11})

        self.assertTrue(self.call("plan_net_change", action="modify", name="N2",
                                  nodes=["Clip2", "Clip12"])["ok"])
        self.assertEqual(nets(self.call("get_state"))["N2"], n2)  # plan did not change it
        self.assertTrue(self.call("modify_net", name="N2", nodes=["Clip2", "Clip12"])["ok"])
        self.assertEqual(nets(self.call("get_state")), {"N1": n1, "N2": ["Clip12", "Clip2"]})

        opened = self.call("all_open")
        self.assertTrue(opened["ok"])
        self.assertEqual(dev.state, "SAFE")
        self.assertFalse(self.call("get_state")["ok"])
        self.assertIsNone(self.call("get_state")["netlist"])
        for tool, args in (("create_net", {"name": "N3", "nodes": ["Clip1", "Clip3"]}),
                           ("guide_leds", {})):
            refused = self.call(tool, **args)
            self.assertFalse(refused["ok"])
            self.assertEqual(refused["error"]["code"], "SESSION_REESTABLISH_REQUIRED")


class FailurePathTests(ServerTestCase):
    def test_unknown_node_passed_through(self):
        for name in ("clip0", "Clip16", "1"):
            with self.subTest(node=name):
                direct = self.call("create_net", name="A", nodes=[name, "Clip1"])
                planned = self.call("plan_net_change", action="create", name="A", nodes=[name, "Clip1"])
                self.assertFalse(direct["ok"])
                self.assertEqual(planned["error"], direct["error"])

    def test_conflict_duplicate_net_and_shared_node(self):
        self.call("create_net", name="A", nodes=["Clip0", "Clip4"])
        dup = self.call("create_net", name="A", nodes=["Clip1", "Clip2"])
        shared = self.call("create_net", name="B", nodes=["Clip4", "Clip5"])
        for result in (dup, shared):
            self.assertFalse(result["ok"])
            self.assertIsNotNone(result["error"]["code"])
            self.assertIsNone(result["netlist"])
        self.assertEqual(self.call("plan_net_change", action="create", name="A",
                                   nodes=["Clip1", "Clip2"])["error"], dup["error"])

    def test_control_blocked_reported_verbatim(self):
        self.call("create_net", name="A", nodes=["Clip0", "Clip4"])
        failure = ApplyResult("timeout", error=HostError("TIMEOUT", "no reply", reason="timeout"))
        with mock.patch.object(self.session._client, "apply", return_value=failure) as apply:
            first = self.call("modify_net", name="A", nodes=["Clip0", "Clip5"])
            self.assertEqual(first["error"]["code"], "TIMEOUT")
            blocked = self.call("create_net", name="B", nodes=["Clip1", "Clip2"])
            self.assertEqual(blocked["error"]["code"], "CONTROL_BLOCKED")
            apply.assert_called_once()  # no retry
        self.assertEqual(self.call("get_state")["error"]["code"], "STATE_UNCONFIRMED")


if __name__ == "__main__":
    unittest.main()
