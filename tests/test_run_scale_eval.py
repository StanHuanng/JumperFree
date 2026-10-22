from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_scale_eval.py"


class ScaleEvaluationCLITests(unittest.TestCase):
    def test_max_seconds_rejects_nonfinite_and_nonpositive_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            for value in ("nan", "inf", "-inf", "0", "-1"):
                with self.subTest(value=value):
                    result = subprocess.run(
                        [sys.executable, str(SCRIPT), "--out", str(output),
                         "--trials", "0", f"--max-seconds={value}"],
                        capture_output=True, text=True, check=False,
                    )
                    self.assertEqual(result.returncode, 2)
                    self.assertIn(
                        "error: --max-seconds must be finite and greater than zero",
                        result.stderr,
                    )
                    self.assertFalse(output.exists())

    def test_max_seconds_accepts_finite_positive_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            timing = Path(directory) / "timing.json"
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--out", str(output),
                 "--timings-out", str(timing), "--trials", "0", "--max-seconds", "30.5"],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["trials"], 0)
            self.assertEqual(
                json.loads(timing.read_text(encoding="utf-8"))["max_seconds"], 30.5
            )


if __name__ == "__main__":
    unittest.main()
