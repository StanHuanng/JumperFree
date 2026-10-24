import unittest
from dataclasses import FrozenInstanceError
from jumperfree.core.vectors import VecDirective, VecError, parse_vec


class DirectiveTests(unittest.TestCase):
    def test_whitelist_and_physical_lines(self):
        text='# 注释\n@ advance_ms 0\n@ fault hw\n@ fault_source_gone hw\n@ reboot por\n@ reboot iwdg\n@ interrupt_next_commit\n> x\n< OK\n'
        steps=parse_vec(text)
        self.assertEqual(steps[0],VecDirective('advance_ms',('0',),2))
        self.assertEqual(steps[-2],VecDirective('interrupt_next_commit',(),7))
        with self.assertRaises(FrozenInstanceError):steps[0].name='x'
        for raw in ['@ advance_ms -1','@ advance_ms 01','@ advance_ms','@ fault power','@ reboot sw',
                    '@ interrupt_next_commit 1','@  fault hw','@ fault hw ', '> x\n@ fault hw\n< OK']:
            with self.subTest(raw=raw),self.assertRaises(VecError):parse_vec(raw)
