"""CLI adapter for the state-file-backed stage-1 routing model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .model import RouteError, create_net, delete_net, init_state, list_nets, query_state

DEFAULT_STATE = Path("out/routing-state.json")


def add_route_parser(subparsers: argparse._SubParsersAction[Any]) -> None:
    route = subparsers.add_parser("route", help="manage the stage-1 software routing model")
    commands = route.add_subparsers(dest="route_command", required=True)

    init = commands.add_parser("init", help="initialize an empty routing state")
    init.add_argument("--nodes", type=int, required=True)
    init.add_argument("--seed", type=int, default=0)
    init.add_argument("--capacity", type=int, default=1)
    init.add_argument("--state", type=Path, default=DEFAULT_STATE)

    create = commands.add_parser("create", help="create and route a new net")
    create.add_argument("--net", required=True)
    create.add_argument("--nodes", required=True, help="comma-separated node numbers")
    create.add_argument("--state", type=Path, default=DEFAULT_STATE)

    delete = commands.add_parser("delete", help="delete a net and free its resources")
    delete.add_argument("--net", required=True)
    delete.add_argument("--state", type=Path, default=DEFAULT_STATE)

    query = commands.add_parser("query", help="query occupancy or one net")
    query.add_argument("--net")
    query.add_argument("--state", type=Path, default=DEFAULT_STATE)

    listing = commands.add_parser("list", help="list routed nets")
    listing.add_argument("--state", type=Path, default=DEFAULT_STATE)


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


def _parse_nodes(value: str) -> list[int]:
    try:
        return [int(part.strip()) for part in value.split(",") if part.strip()]
    except ValueError as exc:
        raise RouteError("INVALID_NODE_LIST", "--nodes must be comma-separated integers") from exc


def _emit(summary: str, payload: dict[str, Any]) -> None:
    print(summary)
    print(f"ROUTE_JSON {json.dumps(payload, sort_keys=True, separators=(',', ':'))}")


def run_route(args: argparse.Namespace) -> int:
    """Execute a parsed route command and return its process status."""
    try:
        if args.route_command == "init":
            state = init_state(args.nodes, args.seed, args.capacity)
            _write_state(args.state, state)
            data: Any = query_state(state)
            summary = f"ROUTE init nodes={args.nodes} capacity={args.capacity} state={args.state}"
        else:
            state = _load_state(args.state)
            if args.route_command == "create":
                state = create_net(state, args.net, _parse_nodes(args.nodes))
                _write_state(args.state, state)
                data = query_state(state, args.net)
                summary = f"ROUTE create net={args.net} nodes={len(data['nodes'])}"
            elif args.route_command == "delete":
                state = delete_net(state, args.net)
                _write_state(args.state, state)
                data = query_state(state)
                summary = f"ROUTE delete net={args.net}"
            elif args.route_command == "query":
                data = query_state(state, args.net)
                summary = f"ROUTE query net={args.net or '*'}"
            else:
                data = list_nets(state)
                summary = f"ROUTE list nets={len(data)}"
        _emit(summary, {"data": data, "ok": True, "operation": args.route_command})
        return 0
    except RouteError as exc:
        _emit(
            f"ROUTE FAIL {exc.code}: {exc.message}",
            {"error": exc.as_dict(), "ok": False, "operation": args.route_command},
        )
        return 1
