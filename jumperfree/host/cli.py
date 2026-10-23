"""CLI adapter for the Stage-1 Clip LED host."""

from __future__ import annotations

import argparse
import json
from typing import Any

from .errors import HostError
from .protocol import validate_request
from .session import HostSession
from .transport import MockTransport, SerialTransport


def add_host_parser(subparsers: argparse._SubParsersAction[Any]) -> None:
    host = subparsers.add_parser("host", help="control Clip0–15 RGB LEDs over JF1 UART")
    commands = host.add_subparsers(dest="host_command", required=True)
    for name in ("set", "clear", "clear-all", "status"):
        command = commands.add_parser(name, help=f"{name} Clip LEDs")
        connection = command.add_mutually_exclusive_group(required=True)
        connection.add_argument("--mock", action="store_true", help="use an in-memory LED peer")
        connection.add_argument("--port", help="USB-UART serial port (requires optional pyserial)")
        command.add_argument("--baudrate", type=int, help="serial only (default: 115200)")
        command.add_argument("--timeout", type=float, help="serial only (default: 1 second)")
        if name in ("set", "clear"):
            command.add_argument("--clip", type=int, required=True)
        if name == "set":
            command.add_argument("--rgb", required=True, help="R,G,B integers in 0..255")


def _parse_rgb(value: str) -> tuple[int, int, int]:
    try:
        parts = value.split(",")
        if len(parts) != 3:
            raise ValueError("expected three channels")
        r, g, b = (int(part.strip()) for part in parts)
        return r, g, b
    except ValueError as exc:
        raise HostError("INVALID_RGB", "--rgb must contain three comma-separated integers") from exc


def run_host(args: argparse.Namespace) -> int:
    try:
        if args.mock and (args.baudrate is not None or args.timeout is not None):
            raise HostError(
                "INVALID_SERIAL_CONFIG", "--baudrate and --timeout cannot be used with --mock",
            )
        rgb = _parse_rgb(args.rgb) if args.host_command == "set" else (0, 0, 0)
        if args.host_command == "set":
            validate_request("SET", (args.clip, *rgb))
        elif args.host_command == "clear":
            validate_request("CLR", (args.clip,))
        transport = (
            MockTransport() if args.mock
            else SerialTransport(
                args.port,
                115200 if args.baudrate is None else args.baudrate,
                1.0 if args.timeout is None else args.timeout,
            )
        )
        session = HostSession(transport)
        primary_error: HostError | None = None
        try:
            if args.host_command == "set":
                response = session.set(args.clip, *rgb)
            elif args.host_command == "clear":
                response = session.clear(args.clip)
            elif args.host_command == "clear-all":
                response = session.clear_all()
            else:
                response = session.status()
        except HostError as exc:
            primary_error = exc
            raise
        finally:
            try:
                session.close()
            except HostError as exc:
                if primary_error is None:
                    raise
                primary_error.details["close_error"] = exc.as_dict()
        summary = f"HOST {args.host_command} OK"
        payload = {"data": response.as_dict(), "ok": True, "operation": args.host_command}
    except HostError as exc:
        summary = f"HOST FAIL {exc.code}: {exc.message}"
        payload = {"error": exc.as_dict(), "ok": False, "operation": args.host_command}
    print(summary)
    print(f"HOST_JSON {json.dumps(payload, sort_keys=True, separators=(',', ':'))}")
    return 0 if payload["ok"] else 1
