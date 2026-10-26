"""Minimal stdio MCP server (hand-written JSON-RPC 2.0) over one AgentSession.

Newline-delimited JSON, single owner, calls handled strictly in order. No SDK:
the subset needed (initialize, ping, tools/list, tools/call) is small and this
keeps the default install dependency-free. Tool results are the tool dicts
verbatim (verified=false); the server never adds, retries or recovers.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import IO, Sequence

from . import tools
from .session import AgentSession, open_session
from .tools import TOOL_SCHEMAS

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "jumperfree", "version": "0.1.0"}

_HANDLERS = {name: getattr(tools, name) for name in TOOL_SCHEMAS}


def _error(msg_id: object, code: int, message: str) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


class McpServer:
    """Dispatch one JSON-RPC message at a time against a caller-owned session."""

    def __init__(self, session: AgentSession) -> None:
        self._session = session

    def handle(self, message: object) -> dict[str, object] | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" \
                or not isinstance(message.get("method"), str):
            return _error(message.get("id") if isinstance(message, dict) else None,
                          -32600, "invalid request")
        method, msg_id = message["method"], message.get("id")
        if "id" not in message:  # notification: never answered
            return None
        params = message.get("params") or {}
        if not isinstance(params, dict):
            return _error(msg_id, -32602, "params must be an object")
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            }}
        if method == "ping":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": msg_id,
                    "result": {"tools": list(TOOL_SCHEMAS.values())}}
        if method == "tools/call":
            return self._call(msg_id, params)
        return _error(msg_id, -32601, f"method not found: {method}")

    def _call(self, msg_id: object, params: dict[str, object]) -> dict[str, object]:
        name, arguments = params.get("name"), params.get("arguments") or {}
        if not isinstance(name, str) or name not in _HANDLERS:
            return _error(msg_id, -32602, f"unknown tool: {name!r}")
        if not isinstance(arguments, dict):
            return _error(msg_id, -32602, "arguments must be an object")
        allowed = set(TOOL_SCHEMAS[name]["inputSchema"]["properties"])
        required = set(TOOL_SCHEMAS[name]["inputSchema"]["required"])
        if set(arguments) - allowed or required - set(arguments):
            # Do not guess or drop arguments; the schema is the contract.
            return _error(msg_id, -32602, f"arguments for {name} must match its schema "
                                          f"(required {sorted(required)}, allowed {sorted(allowed)})")
        result = _HANDLERS[name](self._session, **arguments)
        return {"jsonrpc": "2.0", "id": msg_id, "result": {
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
            "structuredContent": result,
            "isError": not result.get("ok", False),
        }}


def serve(session: AgentSession, stdin: IO[str], stdout: IO[str]) -> None:
    """Read lines until EOF; the caller closes the session."""
    server = McpServer(session)
    for line in stdin:
        if not line.strip():
            continue
        try:
            response = server.handle(json.loads(line))
        except json.JSONDecodeError:
            response = _error(None, -32700, "parse error")
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jumperfree.agent.mcp_server",
                                     description="JumperFree stdio MCP server (verified=false)")
    parser.add_argument("--port", help="serial port; omit to use MockDevice")
    args = parser.parse_args(argv)
    session = open_session("serial", args.port) if args.port else open_session("mock")
    with session:
        serve(session, sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
