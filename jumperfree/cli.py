"""Top-level command-line entry point."""

from __future__ import annotations

import argparse
from typing import Sequence

from .host.cli import add_host_parser, run_host
from .routing.cli import add_route_parser, run_route


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jumperfree", description="JumperFree router and Clip LED host")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_route_parser(subparsers)
    add_host_parser(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "host":
        return run_host(args)
    return run_route(args)
