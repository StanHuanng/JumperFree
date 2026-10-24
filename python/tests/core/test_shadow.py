"""Normative shadow byte/CRC vectors, also intended for the M4 implementation."""

import dataclasses
from types import SimpleNamespace
import unittest

from jumperfree.board import SwitchOp, Xp
from jumperfree.core import JfError
from jumperfree.core.shadow import Shadow, apply_ops, bitmap_hex, parse_bitmap_hex, shadow_bytes, shadow_crc


class ShadowTests(unittest.TestCase):
    def test_hand_computed_layout_and_fixed_crc(self):
        tags = bytearray(256)
        tags[0], tags[1], tags[138] = 1, 2, 5
        shadow = Shadow((3, 1 << 10), bytes(tags))
        expected = (b'\x03' + bytes(15) + b'\x00\x04' + bytes(14)
                    + b'\x21' + bytes(68) + b'\x05' + bytes(58))
        self.assertEqual(len(expected), 160)
        self.assertEqual(shadow_bytes(shadow), expected)
        self.assertEqual(shadow.shadow_bytes(), expected)
        self.assertEqual(shadow_crc(shadow), 0xdab003e1)
        self.assertEqual(shadow.shadow_crc(), 0xdab003e1)
        self.assertEqual(bitmap_hex(shadow, 0), '03' + '00' * 15)
        self.assertEqual(shadow.bitmap_hex(1), '0004' + '00' * 14)
        for chip in range(2):
            self.assertEqual(parse_bitmap_hex(shadow.bitmap_hex(chip)), shadow.closed[chip])
        self.assertEqual(parse_bitmap_hex('ff' * 16), (1 << 128) - 1)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            shadow.closed = (1, 0)

    def test_validation(self):
        for closed in ([0, 0], (0,), (-1, 0), (1 << 128, 0), (False, 0)):
            with self.subTest(closed=closed), self.assertRaises(JfError):
                Shadow(closed)
        for tags in (b'', bytes(255), bytearray(256), bytes([16]) * 256):
            with self.subTest(tags=type(tags)), self.assertRaises(JfError):
                Shadow(tags=tags)
        for value in ('F' * 32, '0' * 31, ' ' * 32, 'g' * 32, None):
            with self.subTest(value=value), self.assertRaises(JfError):
                parse_bitmap_hex(value)
        with self.assertRaises(JfError):
            bitmap_hex(Shadow(), 2)

    def test_strict_bbm_and_clear_tag(self):
        point = Xp(0, 7, 7)
        on, off = SwitchOp(point, True, 15), SwitchOp(point, False, 15)
        made = apply_ops(Shadow(), makes=[on])
        self.assertEqual(made.closed, (1 << 63, 0))
        self.assertEqual(made.tags[63], 15)
        self.assertEqual(apply_ops(made, [off]), Shadow())
        self.assertEqual(apply_ops(made, [off], [on]), made)
        for shadow, breaks, makes in ((Shadow(), [off], []), (made, [], [on]),
                                      (Shadow(), [on], []), (made, [], [off])):
            with self.assertRaises(JfError):
                apply_ops(shadow, breaks, makes)
        bad = SimpleNamespace(xp=SimpleNamespace(chip=0, x=16, y=0), on=True, tag=1)
        with self.assertRaises(JfError):
            apply_ops(Shadow(), makes=[bad])
