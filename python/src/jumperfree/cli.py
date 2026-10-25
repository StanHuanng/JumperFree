"""Top-level command-line entry point."""

from __future__ import annotations

import argparse
from typing import Sequence

from .board.cli import add_plan_parser, run_plan
from .host.cli import add_host_parser, run_host
from .routing.cli import add_route_parser, run_route
from .service.guide import run_guide
from .service.device_demo import run_device_demo
from .service.control_demo import run_control_demo


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jumperfree", description="JumperFree router and Clip LED host")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_plan_parser(add_route_parser(subparsers))
    add_host_parser(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "host":
        if args.host_command == "control-demo":
            return run_control_demo(args)
        if args.host_command == "device-demo":
            return run_device_demo(args)
        if args.host_command == "guide":
            return run_guide(args)
        return run_host(args)
    if args.route_command == "plan":
        return run_plan(args)
    return run_route(args)
