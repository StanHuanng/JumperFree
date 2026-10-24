import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from jumperfree.board import BoardProfile
from jumperfree.board.cli import BANNER
from jumperfree.board.profile import DEFAULT_BOARD_DIR
from jumperfree.cli import main
from jumperfree.core import canonical_json
from jumperfree.routing.steiner import crossbar_create_net, crossbar_init_state


class PlanCliTests(unittest.TestCase):
    def invoke(self, args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = main(['route', 'plan', *args])
        lines = out.getvalue().splitlines()
        data = json.loads(lines[-1].removeprefix('PLAN_JSON '))
        self.assertEqual(lines[-1], 'PLAN_JSON ' + canonical_json(data))
        return result, lines, data

    def test_success_default_old_explicit_old_epoch_and_cli_determinism(self):
        profile = BoardProfile.load()
        empty = crossbar_init_state(profile.topology)
        new = crossbar_create_net(empty, 'a', ['Clip0', 'Clip15'])
        with tempfile.TemporaryDirectory() as tmp:
            old_path, new_path = Path(tmp) / 'old.json', Path(tmp) / 'new.json'
            old_path.write_text(json.dumps(empty))
            new_path.write_text(json.dumps(new))
            args = ['--new', str(new_path), '--base-epoch', '8']
            first = self.invoke(args)
            self.assertEqual(self.invoke(args), first)  # P3 through actual parser/handler
            code, lines, data = first
            self.assertEqual(code, 0)
            self.assertEqual(lines[0], BANNER)
            self.assertRegex(lines[1], r'^PLAN OK breaks=0 makes=2 crc=[0-9a-f]{8}$')
            self.assertEqual(data['base_epoch'], 8)
            self.assertEqual(self.invoke([*args, '--old', str(old_path)])[2], data)
            # A full subprocess catches entrypoint/parser/output regressions too.
            command = [sys.executable, '-m', 'jumperfree', 'route', 'plan', *args]
            a = subprocess.run(command, capture_output=True, text=True, check=True)
            b = subprocess.run(command, capture_output=True, text=True, check=True)
            self.assertEqual(a.stdout, b.stdout)
            self.assertEqual(a.stdout.splitlines(), lines)
            self.assertEqual(new_path.read_text(), json.dumps(new))

    def test_failures_routing_vs_plan_and_require_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'
            for content, expected in ((None, 'STATE_NOT_FOUND'), ('{', 'INVALID_STATE'),
                                      ('[]', 'INVALID_STATE'), ('{}', 'INVALID_STATE')):
                if content is not None:
                    path.write_text(content)
                code, lines, data = self.invoke(['--new', str(path)])
                self.assertEqual(code, 1)
                self.assertEqual(data['error']['code'], expected)
                self.assertEqual(data['error']['layer'], 'routing')
                self.assertEqual(data['error']['state_changed'], 'no')
                self.assertFalse(data['error']['retryable'])
            code, lines, data = self.invoke(['--new', str(path), '--require-verified'])
            self.assertEqual(code, 1)
            self.assertEqual(data['error']['code'], 'BOARD_UNVERIFIED')
            self.assertEqual(data['error']['layer'], 'plan')
            self.assertNotIn(BANNER, lines)
            code, _, data = self.invoke(['--new', str(path), '--board-dir', str(Path(tmp)/'missing')])
            self.assertEqual(code, 1)
            self.assertEqual(data['error']['code'], 'BOARD_INVALID')
            empty = crossbar_init_state(BoardProfile.load().topology)
            path.write_text(json.dumps(empty))
            code, _, data = self.invoke(['--new', str(path), '--base-epoch', '-1'])
            self.assertEqual(code, 1)
            self.assertEqual(data['error']['code'], 'BAD_ARG')
            code, _, data = self.invoke(['--new', str(path), '--old', str(Path(tmp)/'missing')])
            self.assertEqual(code, 1)
            self.assertEqual(data['error']['layer'], 'routing')

    def test_custom_verified_profile_has_no_banner(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            board = directory / 'board'
            shutil.copytree(DEFAULT_BOARD_DIR, board)
            evidence = board / 'docs/hw/test.md'
            evidence.parent.mkdir(parents=True)
            evidence.write_text('Software test fixture only')
            path = board / 'board.toml'
            path.write_text(path.read_text().replace('verified = false',
                            'verified = "hil"\nevidence = "docs/hw/test.md"'))
            state = directory / 'state.json'
            state.write_text(json.dumps(crossbar_init_state(BoardProfile.load().topology)))
            code, lines, _ = self.invoke(['--new', str(state), '--board-dir', str(board), '--require-verified'])
            self.assertEqual(code, 0)
            self.assertEqual(len(lines), 2)

    def test_argparse_errors_exit_two(self):
        for args in ([], ['--new', 'file', '--base-epoch', 'bad']):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                main(['route', 'plan', *args])
            self.assertEqual(caught.exception.code, 2)
