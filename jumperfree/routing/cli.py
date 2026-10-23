"""CLI adapter for the state-file-backed stage-1 routing model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ..core import canonical_json
from .crossbar import small_topology
from .model import RouteError, create_net, delete_net, init_state, list_nets, query_state
from .rearrange import crossbar_create_net_with_rearrange
from .steiner import (
    crossbar_create_net,
    crossbar_delete_net,
    crossbar_init_state,
    crossbar_query,
)
from .v01_topology import v01_topology
from .v5_scale import v5_scale_topology

DEFAULT_STATE = Path("out/routing-state.json")
DEFAULT_CROSSBAR_STATE = Path("out/crossbar-state.json")


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

    xbar = commands.add_parser("xbar", help="manage multi-hop crossbar routing")
    xbar_commands = xbar.add_subparsers(dest="xbar_command", required=True)

    xbar_init = xbar_commands.add_parser("init", help="initialize a crossbar state")
    xbar_init.add_argument("--topology", choices=("small", "v5-scale", "v01"), default="small")
    xbar_init.add_argument("--topology-file", type=Path)
    xbar_init.add_argument("--chips", type=int, default=2)
    xbar_init.add_argument("--x-lines", type=int, default=4)
    xbar_init.add_argument("--y-lines", type=int, default=4)
    xbar_init.add_argument("--state", type=Path, default=DEFAULT_CROSSBAR_STATE)

    xbar_create = xbar_commands.add_parser("create", help="create a crossbar net")
    xbar_create.add_argument("--net", required=True)
    xbar_create.add_argument("--nodes", required=True, help="comma-separated node ids")
    xbar_create.add_argument("--state", type=Path, default=DEFAULT_CROSSBAR_STATE)
    xbar_create.add_argument("--rearrange", action="store_true")
    xbar_create.add_argument("--max-moves", type=int, default=8)
    xbar_create.add_argument("--max-states", type=int, default=2000)
    xbar_create.add_argument("--max-rip-nets", type=int, default=3)

    xbar_delete = xbar_commands.add_parser("delete", help="delete a crossbar net")
    xbar_delete.add_argument("--net", required=True)
    xbar_delete.add_argument("--state", type=Path, default=DEFAULT_CROSSBAR_STATE)

    xbar_query = xbar_commands.add_parser("query", help="query crossbar occupancy or one net")
    xbar_query.add_argument("--net")
    xbar_query.add_argument("--state", type=Path, default=DEFAULT_CROSSBAR_STATE)


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


def _run_crossbar(
    args: argparse.Namespace,
) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
    if args.xbar_command == "init":
        if args.topology_file is not None:
            topology = _load_topology(args.topology_file)
        elif args.topology == "v5-scale":
            topology = v5_scale_topology()
        elif args.topology == "v01":
            topology = v01_topology()
        else:
            topology = small_topology(args.chips, args.x_lines, args.y_lines)
        state = crossbar_init_state(topology)
        _write_state(args.state, state)
        data = crossbar_query(state)
        return f"ROUTE xbar init lines={data['occupancy']['total']} state={args.state}", data, None

    state = _load_state(args.state)
    if args.xbar_command == "create":
        nodes = _parse_crossbar_nodes(args.nodes)
        stats = None
        if args.rearrange:
            state, stats = crossbar_create_net_with_rearrange(
                state,
                args.net,
                nodes,
                max_moves=args.max_moves,
                max_states=args.max_states,
                max_rip_nets=args.max_rip_nets,
            )
        else:
            state = crossbar_create_net(state, args.net, nodes)
        _write_state(args.state, state)
        data = crossbar_query(state, args.net)
        return f"ROUTE xbar create net={args.net} nodes={len(data['nodes'])}", data, stats
    if args.xbar_command == "delete":
        state = crossbar_delete_net(state, args.net)
        _write_state(args.state, state)
        return f"ROUTE xbar delete net={args.net}", crossbar_query(state), None
    data = crossbar_query(state, args.net)
    return f"ROUTE xbar query net={args.net or '*'}", data, None


def run_route(args: argparse.Namespace) -> int:
    """Execute a parsed route command and return its process status."""
    try:
        if args.route_command == "xbar":
            summary, data, rearrange_stats = _run_crossbar(args)
            operation = args.xbar_command
        elif args.route_command == "init":
            state = init_state(args.nodes, args.seed, args.capacity)
            _write_state(args.state, state)
            data: Any = query_state(state)
            summary = f"ROUTE init nodes={args.nodes} capacity={args.capacity} state={args.state}"
            operation = args.route_command
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
            operation = args.route_command
        payload = {"data": data, "ok": True, "operation": operation}
        if args.route_command == "xbar" and rearrange_stats is not None:
            payload["rearrange"] = rearrange_stats
        _emit(summary, payload)
        return 0
    except RouteError as exc:
        operation = getattr(args, "xbar_command", None) or args.route_command
        _emit(
            f"ROUTE FAIL {exc.code}: {exc.message}",
            {"error": exc.as_dict(), "ok": False, "operation": operation},
        )
        return 1
