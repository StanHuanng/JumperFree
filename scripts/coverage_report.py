#!/usr/bin/env python3
"""Standard-library line-coverage report for the ``jumperfree`` package.

Runs the full ``unittest`` suite under :mod:`trace` and writes a per-module line
coverage summary to ``out/coverage.txt``. It relies only on the standard library,
so ``python3 scripts/coverage_report.py`` works without any third-party package. The optional
``coverage.py`` extra can produce richer HTML/XML reports but is not required.

The script exits non-zero unless the suite passes and line coverage of
``jumperfree/**/*.py`` is exactly 100%.
"""

from __future__ import annotations

import sys
import trace
import unittest
from io import StringIO
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PKG_DIR = REPO_ROOT / "jumperfree"
TESTS_DIR = REPO_ROOT / "tests"
OUT_DIR = REPO_ROOT / "out"
OUTPUT = OUT_DIR / "coverage.txt"

# When this file is run directly, the repo root is not
# automatically on sys.path. Add it so `unittest discover` can import jumperfree.
sys.path.insert(0, str(REPO_ROOT))


def _executable_linenos(path: Path) -> set[int]:
    """Return the executable line numbers of a source file (stdlib trace helper)."""
    return {
        lineno
        for lineno, is_executable in trace._find_executable_linenos(str(path)).items()
        if is_executable and isinstance(lineno, int) and lineno >= 1
    }


def main() -> int:
    # NOTE: pass an empty ignoredirs list. ``trace`` deduplicates package
    # ``__init__.py`` files under the single module name ``__init__`` (see the
    # "doesn't work right for packages" note in trace.py), so ignoring the
    # stdlib directory would also wrongly ignore ``jumperfree/__init__.py``.
    # The report filters results to ``jumperfree/*.py`` anyway.
    tracer = trace.Trace(count=True, trace=False, ignoredirs=[])

    def _run_suite() -> unittest.result.TestResult:
        suite = unittest.defaultTestLoader.discover(str(TESTS_DIR))
        runner = unittest.TextTestRunner(stream=StringIO(), verbosity=0)
        return runner.run(suite)

    result = tracer.runfunc(_run_suite)

    counts = tracer.results().counts
    source_files = sorted(PKG_DIR.rglob("*.py"))

    rows: list[tuple[int, int, str]] = []
    total_executable = 0
    total_covered = 0
    for path in source_files:
        filename = str(path)
        executable = _executable_linenos(path)
        covered = {
            lineno
            for (covered_filename, lineno), count in counts.items()
            if covered_filename == filename and count > 0 and lineno in executable
        }
        n_executable = len(executable)
        n_covered = len(covered)
        total_executable += n_executable
        total_covered += n_covered
        rows.append((n_executable, n_covered, str(path.relative_to(REPO_ROOT))))

    width = max(len(module) for _, _, module in rows) if rows else 0
    lines = [
        "JumperFree line coverage (standard-library trace)",
        "",
        f"{'lines':>6} {'cov%':>6}  {'module':<{width}} covered",
    ]
    for n_executable, n_covered, module in rows:
        pct = 100.0 * n_covered / n_executable if n_executable else 0.0
        lines.append(
            f"{n_executable:>6} {pct:>6.1f}  {module:<{width}} {n_covered}/{n_executable}"
        )
    overall = 100.0 * total_covered / total_executable if total_executable else 0.0
    lines.extend([
        "-" * (width + 24),
        f"{total_executable:>6} {overall:>6.1f}  {'jumperfree/**/*.py':<{width}} "
        f"{total_covered}/{total_executable}",
    ])

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUTPUT.read_text(encoding="utf-8"), end="")
    print(f"COVERAGE_WRITTEN {OUTPUT}")

    if not result.wasSuccessful():
        print("WARNING: test suite failed; coverage numbers reflect partial execution",
              file=sys.stderr)
        return 1
    if total_covered != total_executable:
        print("ERROR: line coverage below 100%", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
