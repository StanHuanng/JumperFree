#!/usr/bin/env python3
"""统计 V0.1 随机创建/删除工作负载的计划大小（不用于 V5-scale）。"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jumperfree.board.stats import run_plan_stats
from jumperfree.core import canonical_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--trials", type=int, default=60)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.trials < 0:
        parser.error("--trials must be non-negative")
    report = run_plan_stats(seed=args.seed, trials=args.trials)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(canonical_json(report) + "\n", encoding="utf-8")
    sizes = report["ops_per_step"]
    print(f"PLAN_STATS v01 min={sizes['min']} mean={sizes['mean']:.3f} max={sizes['max']} "
          f"max_ops={report['max_ops']} exceeding={report['steps_exceeding_max_ops']}")
    return int(report["steps_exceeding_max_ops"] != 0)


if __name__ == "__main__":
    raise SystemExit(main())
