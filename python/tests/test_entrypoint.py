from __future__ import annotations

import contextlib
import io
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class EntrypointTests(unittest.TestCase):
    def test_python_dash_m_entrypoint_runs_route_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = str(Path(directory) / "state.json")
            argv = ["jumperfree", "route", "xbar", "init", "--topology", "v01", "--state", state]
            output = io.StringIO()
            with mock.patch("sys.argv", argv), contextlib.redirect_stdout(output):
                with self.assertRaises(SystemExit) as raised:
                    runpy.run_module("jumperfree", run_name="__main__")
            self.assertEqual(raised.exception.code, 0)
            self.assertIn("ROUTE_JSON ", output.getvalue())


if __name__ == "__main__":
    unittest.main()
