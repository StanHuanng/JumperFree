import dataclasses
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from jumperfree.board import BoardProfile, PlanError, Xp
from jumperfree.board.profile import DEFAULT_BOARD_DIR, reject_bad_crosspoints
from jumperfree.core import canonical_json
from jumperfree.routing.v01_topology import v01_topology


class ProfileTests(unittest.TestCase):
    def test_packaged_topology_identity_and_detachment(self):
        profile = BoardProfile.load()
        self.assertEqual(profile.topology, v01_topology())
        self.assertEqual(json.loads((DEFAULT_BOARD_DIR / 'topology.json').read_text()), v01_topology())
        expected = hashlib.sha256(canonical_json({'topology': v01_topology(),
                                                  'known_bad_crosspoints': []}).encode()).hexdigest()[:16]
        self.assertEqual(profile.topology_id, expected)
        self.assertEqual(dict(profile.chip_map), {'C1': 0, 'C2': 1})
        detached = profile.topology
        detached['chips'].clear()
        self.assertEqual(profile.topology, v01_topology())
        with self.assertRaises(dataclasses.FrozenInstanceError):
            profile.rev = 'changed'
        with self.assertRaises(PlanError) as caught:
            profile.require_verified()
        self.assertEqual(caught.exception.code, 'BOARD_UNVERIFIED')
        self.assertEqual(len(profile.unverified_sections), 4)

    def test_bad_points_normalized_and_identity_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            shutil.copytree(DEFAULT_BOARD_DIR, directory, dirs_exist_ok=True)
            (directory / 'calibration.json').write_text(json.dumps({
                'schema': 'jf.calib/1', 'measurements': [],
                'known_bad_crosspoints': [[1, 2, 3], [0, 1, 2], [1, 2, 3]],
            }))
            profile = BoardProfile.load(str(directory))
            self.assertEqual(profile.known_bad_crosspoints, (Xp(0, 1, 2), Xp(1, 2, 3)))
            self.assertNotEqual(profile.topology_id, BoardProfile.load().topology_id)
            reject_bad_crosspoints({}, profile)
            with self.assertRaises(PlanError) as caught:
                reject_bad_crosspoints({Xp(1, 2, 3): 'net'}, profile)
            self.assertEqual(caught.exception.code, 'PLAN_BAD_CROSSPOINT')

    def test_verified_requires_local_existing_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            shutil.copytree(DEFAULT_BOARD_DIR, directory, dirs_exist_ok=True)
            original = (directory / 'board.toml').read_text()
            for value in ('true', '"wrong"', '"bench"', '"hil"'):
                (directory / 'board.toml').write_text(original.replace('false', value))
                with self.assertRaises(PlanError) as caught:
                    BoardProfile.load(directory)
                self.assertEqual(caught.exception.code, 'BOARD_INVALID')
            for evidence in ('/tmp/proof', '../proof', 'other/proof', 'docs/hw/missing'):
                text = original.replace('verified = false', f'verified = "bench"\nevidence = "{evidence}"')
                (directory / 'board.toml').write_text(text)
                with self.assertRaises(PlanError):
                    BoardProfile.load(directory)
            evidence = directory / 'docs/hw/proof.md'
            evidence.parent.mkdir(parents=True)
            evidence.write_text('测试夹具证据，仅用于软件单测，不是出厂配置')
            text = original.replace('verified = false', 'verified = "bench"\nevidence = "docs/hw/proof.md"')
            (directory / 'board.toml').write_text(text)
            profile = BoardProfile.load(directory)
            profile.require_verified()
            self.assertEqual(profile.unverified_sections, ())

    def test_invalid_config_errors(self):
        with self.assertRaises(PlanError):
            BoardProfile.load('/does-not-exist')
        for filename, value in (('board.toml', 'schema="wrong"\nboard_id="v01"\nrev="0.1"'),
                                ('limits.toml', 'max_ops=-1'), ('topology.json', '{}'),
                                ('calibration.json', '{'), ('pinmap.toml', '=')):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp)
                shutil.copytree(DEFAULT_BOARD_DIR, directory, dirs_exist_ok=True)
                (directory / filename).write_text(value)
                with self.assertRaises(PlanError) as caught:
                    BoardProfile.load(directory)
                self.assertEqual(caught.exception.code, 'BOARD_INVALID')
