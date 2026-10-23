"""CI evidence gate, using temporary configuration only; never alters shipped verification."""

import contextlib
import io
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from jumperfree.board.profile import DEFAULT_BOARD_DIR

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/check_board_verification.py'


class BoardVerificationTests(unittest.TestCase):
    def test_shipped_unverified_configuration(self):
        main = runpy.run_path(str(SCRIPT))['main']
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main([]), 0)

    def test_gate_requires_changed_evidence_and_handles_git_failure(self):
        main = runpy.run_path(str(SCRIPT))['main']
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            shutil.copytree(DEFAULT_BOARD_DIR, directory, dirs_exist_ok=True)
            board = directory / 'board.toml'
            board.write_text(board.read_text().replace('verified = false',
                             'verified = "bench"\nevidence = "docs/hw/proof.md"'))
            evidence = directory / 'docs/hw/proof.md'
            evidence.parent.mkdir(parents=True)
            evidence.write_text('Software test fixture only')
            args = ['--board-dir', str(directory)]
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(args), 1)
                with patch('subprocess.check_output', return_value=''):
                    self.assertEqual(main([*args, '--base', 'test-base']), 1)
                with patch('subprocess.check_output', side_effect=subprocess.CalledProcessError(1, 'git')):
                    self.assertEqual(main([*args, '--base', 'test-base']), 1)
            with patch('subprocess.check_output', return_value=str(evidence)+'\n') as diff:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main([*args, '--base', 'test-base']), 0)
                self.assertIn('test-base...HEAD', diff.call_args.args[0])
