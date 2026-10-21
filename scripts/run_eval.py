#!/usr/bin/env python3
"""Run the deterministic small-topology routing evaluation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from jumperfree.routing.eval import run_evaluation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--trials", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-moves", type=int, default=8)
    parser.add_argument("--max-states", type=int, default=2000)
    args = parser.parse_args()
    report = run_evaluation(
        seed=args.seed,
        trials=args.trials,
        max_moves=args.max_moves,
        max_states=args.max_states,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"EVAL_WRITTEN {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
