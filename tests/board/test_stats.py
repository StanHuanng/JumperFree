from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from jumperfree.board import BoardProfile, Limits
from jumperfree.board.stats import run_plan_stats, v01_transitions
from jumperfree.routing.model import RouteError


class StatsTests(unittest.TestCase):
    def test_stats_deterministic_limit_and_empty(self):
        report = run_plan_stats(seed=7, trials=20)
        self.assertEqual(report, run_plan_stats(seed=7, trials=20))
        self.assertEqual(report['steps_exceeding_max_ops'], 0)
        self.assertEqual(report['ops_per_step']['min'], min(report['sizes']))
        self.assertEqual(report['ops_per_step']['mean'], sum(report['sizes'])/20)
        self.assertEqual(report['ops_per_step']['max'], max(report['sizes']))
        self.assertEqual(run_plan_stats(trials=0)['ops_per_step'], {'min': 0, 'mean': 0.0, 'max': 0})
        self.assertEqual(run_plan_stats(trials=3, profile=replace(BoardProfile.load(), limits=Limits(max_ops=0)))
                         ['steps_exceeding_max_ops'], 3)

    def test_generator_validation_and_failed_routes(self):
        profile = BoardProfile.load()
        for kwargs in ({'trials': -1}, {'trials': True}, {'seed': False}):
            with self.assertRaises(ValueError):
                list(v01_transitions(profile, **kwargs))
        with patch('jumperfree.board.stats.crossbar_create_net_with_rearrange',
                   side_effect=RouteError('NO_FREE_PATH', 'test failure')):
            pairs = list(v01_transitions(profile, trials=3))
            self.assertTrue(all(old['nets'] == new['nets'] for old, new in pairs))
        with patch('jumperfree.board.stats.crossbar_create_net_with_rearrange',
                   side_effect=RouteError('INVALID_STATE', 'unexpected')):
            with self.assertRaises(RouteError):
                list(v01_transitions(profile, trials=1))

    def test_script(self):
        script = Path(__file__).resolve().parents[2] / 'scripts/run_plan_stats.py'
        with tempfile.TemporaryDirectory() as tmp:
            output = str(Path(tmp) / 'stats.json')
            result = subprocess.run([sys.executable, str(script), '--out', output, '--trials', '3'],
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('PLAN_STATS v01', result.stdout)
            result = subprocess.run([sys.executable, str(script), '--out', output, '--trials', '-1'],
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 2)
