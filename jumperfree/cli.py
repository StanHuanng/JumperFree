"""Top-level command-line entry point."""

from __future__ import annotations

import argparse
from typing import Sequence

from .routing.cli import add_route_parser, run_route


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jumperfree", description="JumperFree software router")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_route_parser(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_route(args)
