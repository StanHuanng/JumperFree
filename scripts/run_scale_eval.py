#!/usr/bin/env python3
"""Run the deterministic synthetic V5-scale routing evaluation."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from jumperfree.routing.scale_eval import run_scale_evaluation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--trials", type=int, default=60)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-moves", type=int, default=64)
    parser.add_argument("--max-states", type=int, default=32)
    parser.add_argument("--max-rip-nets", type=int, default=3)
    parser.add_argument("--timings-out", type=Path)
    parser.add_argument("--max-seconds", type=float, default=30.0)
    args = parser.parse_args()
    if args.max_seconds <= 0:
        parser.error("--max-seconds must be greater than zero")

    started = time.perf_counter()
    report = run_scale_evaluation(
        seed=args.seed,
        trials=args.trials,
        max_moves=args.max_moves,
        max_states=args.max_states,
        max_rip_nets=args.max_rip_nets,
    )
    elapsed = time.perf_counter() - started
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.timings_out is not None:
        args.timings_out.parent.mkdir(parents=True, exist_ok=True)
        timing = {"elapsed_seconds": elapsed, "max_seconds": args.max_seconds}
        args.timings_out.write_text(
            json.dumps(timing, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(f"SCALE_EVAL_WRITTEN {args.out} elapsed={elapsed:.3f}s")
    if elapsed > args.max_seconds:
        print(
            f"SCALE_EVAL_TIMEOUT elapsed={elapsed:.3f}s max={args.max_seconds:.3f}s",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
