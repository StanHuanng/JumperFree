"""Board plan CLI boundary; routing never imports this module."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ..core import JfError, canonical_json, error_envelope
from ..routing.cli import _load_state
from ..routing.model import RouteError
from ..routing.steiner import crossbar_init_state, crossbar_query
from .plan import compile_plan
from .profile import BoardProfile

BANNER = "警告：拓扑/引脚未经实测（verified=false）"


def add_plan_parser(commands: argparse._SubParsersAction[Any]) -> None:
    parser = commands.add_parser("plan", help="编译并校验 V0.1 先断后通计划")
    parser.add_argument("--new", type=Path, required=True)
    parser.add_argument("--old", type=Path)
    parser.add_argument("--base-epoch", type=int, default=0)
    parser.add_argument("--board-dir", type=Path)
    parser.add_argument("--require-verified", action="store_true")


def run_plan(args: argparse.Namespace) -> int:
    try:
        profile = BoardProfile.load(args.board_dir)
        if args.require_verified:
            profile.require_verified()
        if profile.unverified_sections:
            print(BANNER)
        new = _load_state(args.new)
        crossbar_query(new)
        old = crossbar_init_state(new["topology"]) if args.old is None else _load_state(args.old)
        plan = compile_plan(old, new, profile, base_epoch=args.base_epoch)
        print(f"PLAN OK breaks={len(plan.breaks)} makes={len(plan.makes)} crc={plan.expect_crc:08x}")
        print("PLAN_JSON " + canonical_json(plan.to_json()))
        return 0
    except JfError as exc:
        print(f"PLAN FAIL {exc.code}: {exc.message}")
        print("PLAN_JSON " + canonical_json(error_envelope(
            exc.code, "routing" if isinstance(exc, RouteError) else "plan", exc.message, exc.details,
        )))
        return 1
