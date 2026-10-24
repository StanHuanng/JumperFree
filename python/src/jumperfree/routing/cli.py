"""CLI adapter for the state-file-backed crossbar routing model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ..core import canonical_json
from .crossbar import small_topology
from .model import RouteError
from .steiner import (
    crossbar_create_net,
    crossbar_delete_net,
    crossbar_init_state,
    crossbar_query,
)
from .v01_topology import v01_topology

DEFAULT_CROSSBAR_STATE = Path("out/crossbar-state.json")


def add_route_parser(subparsers: argparse._SubParsersAction[Any]) -> argparse._SubParsersAction[Any]:
    route = subparsers.add_parser("route", help="manage crossbar routing state")
    commands = route.add_subparsers(dest="route_command", required=True)

    xbar = commands.add_parser("xbar", help="manage multi-hop crossbar routing")
    xbar_commands = xbar.add_subparsers(dest="xbar_command", required=True)

    xbar_init = xbar_commands.add_parser("init", help="initialize a crossbar state")
    xbar_init.add_argument("--topology", choices=("small", "v01"), default="small")
    xbar_init.add_argument("--topology-file", type=Path)
    xbar_init.add_argument("--chips", type=int, default=2)
    xbar_init.add_argument("--x-lines", type=int, default=4)
    xbar_init.add_argument("--y-lines", type=int, default=4)
    xbar_init.add_argument("--state", type=Path, default=DEFAULT_CROSSBAR_STATE)

    xbar_create = xbar_commands.add_parser("create", help="create a crossbar net")
    xbar_create.add_argument("--net", required=True)
    xbar_create.add_argument("--nodes", required=True, help="comma-separated node ids")
    xbar_create.add_argument("--state", type=Path, default=DEFAULT_CROSSBAR_STATE)

    xbar_delete = xbar_commands.add_parser("delete", help="delete a crossbar net")
    xbar_delete.add_argument("--net", required=True)
    xbar_delete.add_argument("--state", type=Path, default=DEFAULT_CROSSBAR_STATE)

    xbar_query = xbar_commands.add_parser("query", help="query crossbar occupancy or one net")
    xbar_query.add_argument("--net")
    xbar_query.add_argument("--state", type=Path, default=DEFAULT_CROSSBAR_STATE)
    return commands


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RouteError("STATE_NOT_FOUND", f"routing state not found: {path}") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RouteError("INVALID_STATE", f"routing state is unreadable: {path}") from exc
    if not isinstance(value, dict):
        raise RouteError("INVALID_STATE", "routing state root must be an object")
    return value


def _parse_crossbar_nodes(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _load_topology(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RouteError("INVALID_TOPOLOGY", f"topology file not found: {path}") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RouteError("INVALID_TOPOLOGY", f"topology file is unreadable: {path}") from exc


def _emit(summary: str, payload: dict[str, Any]) -> None:
    print(summary)
    print(f"ROUTE_JSON {canonical_json(payload)}")


def _run_crossbar(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    if args.xbar_command == "init":
        if args.topology_file is not None:
            topology = _load_topology(args.topology_file)
        elif args.topology == "v01":
            topology = v01_topology()
        else:
            topology = small_topology(args.chips, args.x_lines, args.y_lines)
        state = crossbar_init_state(topology)
        _write_state(args.state, state)
        data = crossbar_query(state)
        return f"ROUTE xbar init lines={data['occupancy']['total']} state={args.state}", data

    state = _load_state(args.state)
    if args.xbar_command == "create":
        state = crossbar_create_net(state, args.net, _parse_crossbar_nodes(args.nodes))
        _write_state(args.state, state)
        data = crossbar_query(state, args.net)
        return f"ROUTE xbar create net={args.net} nodes={len(data['nodes'])}", data
    if args.xbar_command == "delete":
        state = crossbar_delete_net(state, args.net)
        _write_state(args.state, state)
        return f"ROUTE xbar delete net={args.net}", crossbar_query(state)
    data = crossbar_query(state, args.net)
    return f"ROUTE xbar query net={args.net or '*'}", data


def run_route(args: argparse.Namespace) -> int:
    """Execute a parsed route command and return its process status."""
    try:
        summary, data = _run_crossbar(args)
        _emit(summary, {"data": data, "ok": True, "operation": args.xbar_command})
        return 0
    except RouteError as exc:
        _emit(
            f"ROUTE FAIL {exc.code}: {exc.message}",
            {"error": exc.as_dict(), "ok": False, "operation": args.xbar_command},
        )
        return 1
