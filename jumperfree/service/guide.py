"""Read routing intent, compile board LEDs and drive a JF1 host session."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..board import LedPlanError, plan_guide
from ..core import canonical_json
from ..host import HostError, HostSession, MockTransport, SerialTransport


def _load_guide(path: Path) -> list[tuple[int, int, int, int]]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HostError("STATE_NOT_FOUND", f"routing state not found: {path}") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HostError("INVALID_STATE", f"routing state is unreadable: {path}") from exc
    try:
        return plan_guide(state)
    except LedPlanError as exc:
        raise HostError(exc.code, exc.message, **exc.details) from exc


def run_guide(args: argparse.Namespace) -> int:
    """Run the legacy host guide command, preserving output and failure ordering."""
    try:
        if args.mock and (args.baudrate is not None or args.timeout is not None):
            raise HostError(
                "INVALID_SERIAL_CONFIG", "--baudrate and --timeout cannot be used with --mock",
            )
        plan = _load_guide(args.state)
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
            session.clear_all()
            for clip, r, g, b in plan:
                session.set(clip, r, g, b)
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
        summary = "HOST guide OK"
        payload = {"data": {"plan": plan}, "ok": True, "operation": "guide"}
    except HostError as exc:
        summary = f"HOST FAIL {exc.code}: {exc.message}"
        payload = {"error": exc.as_dict(), "ok": False, "operation": "guide"}
    print(summary)
    print(f"HOST_JSON {canonical_json(payload)}")
    return 0 if payload["ok"] else 1
