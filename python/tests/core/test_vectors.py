from __future__ import annotations

from dataclasses import FrozenInstanceError
import unittest

from jumperfree.core import JfError
from jumperfree.core.vectors import VecError, VecStep, match_response, parse_vec


class VecParserTests(unittest.TestCase):
    def test_empty_and_comment_only_files(self) -> None:
        for text in ("", "\n \t\n# 中文注释\n", "# comment\r\n\r\n"):
            with self.subTest(text=text):
                self.assertEqual(parse_vec(text), ())

    def test_steps_keep_physical_request_lines_and_exact_payload(self) -> None:
        text = "# 注释\r\n> JF1 STATUS\r\n\n# 中间注释\n< JF1 OK STATUS 0\n> \n< ERR empty"
        self.assertEqual(parse_vec(text), (
            VecStep(b"JF1 STATUS\n", ("JF1", "OK", "STATUS", "0"), 2),
            VecStep(b"\n", ("ERR", "empty"), 6),
        ))
        self.assertEqual(parse_vec(">  a  \n< OK # literal\n")[0].request, b" a  \n")
        step = parse_vec("> request\n< response")[0]
        with self.assertRaises(FrozenInstanceError):
            step.line = 10

    def test_escapes_are_bytes_not_unicode_or_recursive_substitutions(self) -> None:
        step, = parse_vec(r"> JF1 STATUS\r" + "\n" + r"< JF1\tOK STATUS\r")
        self.assertEqual(step.request, b"JF1 STATUS\r\n")
        self.assertEqual(step.expected, ("JF1", "OK", "STATUS"))
        step, = parse_vec(r"> \\\r\t\x00\x7f\xAf\xFF\x20\x414" + "\n< OK")
        self.assertEqual(step.request, b"\\\r\t\x00\x7f\xaf\xff A4\n")
        step, = parse_vec(r"> \\x41" + "\n" + r"< \\ literal\x2A key=*")
        self.assertEqual(step.request, b"\\x41\n")
        self.assertEqual(step.expected, ("\\", "literal*", "key=*"))
        step, = parse_vec("> request\n" + r"< a\x1cb\x1dc\x1ed\x1fe\x00f\x0bg\x0ch")
        self.assertEqual(step.expected, ("a\x1cb\x1dc\x1ed\x1fe\x00f", "g", "h"))

    def test_syntax_errors_include_code_source_and_physical_line(self) -> None:
        cases = (
            ("garbage", 1, "行必须"),
            (">", 1, "行必须"),
            ("<", 1, "行必须"),
            (" > request", 1, "行必须"),
            (" # comment", 1, "行必须"),
            ("< OK", 1, "之前缺少请求"),
            ("> a\n< OK\n< extra", 3, "之前缺少请求"),
            ("> a\n> b\n< OK", 2, "上一个请求"),
            ("# comment\n> a\n# end\n", 2, "请求缺少"),
            ("> a\n< ", 2, "至少需要一个 token"),
            ("> a\n< \\t\\r", 2, "至少需要一个 token"),
            ("@ fault broken", 1, "未知指令"),
            ("> a\n@ advance_ms 10\n< OK", 2, "未知指令"),
            ("@", 1, "未知指令"),
            ("> 中文\n< OK", 1, "可打印 ASCII"),
            ("> a\t\n< OK", 1, "可打印 ASCII"),
            ("> a\rbroken\n< OK", 1, "可打印 ASCII"),
            ("> \x00\n< OK", 1, "可打印 ASCII"),
            ("> a\n< \\xff", 2, "预期应答必须是 ASCII"),
            ("> \\x0a\n< OK", 1, "内嵌 LF"),
            ("> a\n< OK\\x0A", 2, "内嵌 LF"),
        )
        for payload in ("\\", "\\n", "\\b", "\\0", "\\X41", "\\u0041", "\\x", "\\x1", "\\xGG", "\\x0g"):
            cases += (("> " + payload + "\n< OK", 1, "转义" if not payload.startswith("\\x") else "十六进制"),)
            cases += (("> a\n< " + payload, 2, "转义" if not payload.startswith("\\x") else "十六进制"),)
        for text, line, fragment in cases:
            with self.subTest(text=text), self.assertRaises(VecError) as raised:
                parse_vec(text, name="broken.vec")
            error = raised.exception
            self.assertIsInstance(error, JfError)
            self.assertEqual(error.code, "VEC_SYNTAX")
            self.assertEqual(error.details, {"name": "broken.vec", "line": line})
            self.assertIn(f"broken.vec:{line}:", str(error))
            self.assertIn(fragment, error.message)
        with self.assertRaisesRegex(VecError, r"<vec>:1:"):
            parse_vec("> missing response")


class VecMatcherTests(unittest.TestCase):
    def test_token_comparison_and_line_endings(self) -> None:
        for actual in (b"JF1 OK STATUS\n", b"JF1 OK STATUS\r\n", b"JF1 OK STATUS", b" JF1\tOK  STATUS \r\n"):
            with self.subTest(actual=actual):
                self.assertTrue(match_response(("JF1", "OK", "STATUS"), actual))
        self.assertTrue(match_response((), b"\n"))
        self.assertTrue(match_response(("a\x1cb\x1dc\x1ed\x1fe\x00f", "g", "h"),
                                       b"a\x1cb\x1dc\x1ed\x1fe\x00f\x0bg\x0ch\n"))
        self.assertFalse(match_response(("a", "b"), b"a\x1cb\n"))

    def test_single_token_and_key_value_wildcards(self) -> None:
        self.assertTrue(match_response(("JF2", "*", "boot=*", "epoch=0"), b"JF2 OK boot=ab12 epoch=0\n"))
        self.assertTrue(match_response(["*", "key=*"], b"anything key=a=b\n"))
        self.assertTrue(match_response(("abc*", "=*", "key=*tail"), b"abc* =* key=*tail\n"))
        for expected, actual in (
            (("*",), b"\n"),
            (("*",), b"two tokens\n"),
            (("key=*",), b"key=\n"),
            (("key=*",), b"other=value\n"),
            (("key=*",), b"key\n"),
            (("abc*",), b"abcdef\n"),
            (("=*",), b"=value\n"),
            (("key=*tail",), b"key=value-tail\n"),
            (("JF1", "OK"), b"JF1 ERR\n"),
            (("OK",), b"OK extra\n"),
            (("OK",), b"\xff\n"),
            (("*",), b"\xff\n"),
            (("OK",), b"OK\n\n"),
            (("OK", "OK"), b"OK\nOK\n"),
        ):
            with self.subTest(expected=expected, actual=actual):
                self.assertFalse(match_response(expected, actual))
