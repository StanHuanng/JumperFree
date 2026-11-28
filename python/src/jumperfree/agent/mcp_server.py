"""stdio MCP server for JumperFree (hand-written JSON-RPC 2.0, no SDK).

One process = one Agent conversation = one AgentSession. Newline-delimited JSON
on stdin/stdout, one owner, requests handled strictly in order. Logs go to
stderr. On EOF, SIGTERM or Ctrl-C the session closes: LEDs off, SW ALLOPEN.

Register the same command in Claude Code, Cursor or Codex:
    jumperfree-mcp            (search the board on USB)
    jumperfree-mcp --port COM4
    jumperfree-mcp --mock     (in-memory JumperFree mini, no hardware)
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
from typing import IO, Sequence

from .. import __version__
from . import tools
from .session import LEASE_MS, AgentSession, open_session
from .tools import INSTRUCTIONS, TOOL_SCHEMAS

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "jumperfree", "version": __version__}

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
            asked = params.get("protocolVersion")
            version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
            return {"jsonrpc": "2.0", "id": msg_id, "result": {
                "protocolVersion": version,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
                "instructions": INSTRUCTIONS,
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


def _stop_on_signals() -> None:
    def stop(signum, frame):  # noqa: ARG001 - signal handler signature
        raise SystemExit(0)
    for name in ("SIGTERM", "SIGBREAK", "SIGHUP"):
        if hasattr(signal, name):
            try:
                signal.signal(getattr(signal, name), stop)
            except (OSError, ValueError):
                pass


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jumperfree-mcp",
                                     description="JumperFree Host: stdio MCP server for local Agents")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--port", default="auto",
                        help="serial port such as COM4 or /dev/ttyUSB0; default: search USB (auto)")
    source.add_argument("--mock", action="store_true", help="use an in-memory board, no hardware")
    parser.add_argument("--board", choices=["mini", "v01"],
                        help="expected board (mock: which board to simulate, default mini)")
    parser.add_argument("--lease-ms", type=int, default=LEASE_MS, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    session = (open_session("mock", board=args.board, lease_ms=args.lease_ms) if args.mock
               else open_session("auto", None if args.port == "auto" else args.port,
                                 board=args.board, lease_ms=args.lease_ms))
    _stop_on_signals()
    try:
        serve(session, sys.stdin, sys.stdout)
    except KeyboardInterrupt:
        pass
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
