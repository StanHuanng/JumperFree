from __future__ import annotations

from pathlib import Path
import unittest

from jumperfree.core.vectors import VecStep, match_response, parse_vec
from jumperfree.host.transport import MockTransport


def load_vec(path: Path) -> tuple[VecStep, ...]:
    """Keep filesystem access in the consumer, outside the pure core parser."""
    return parse_vec(path.read_text(encoding="utf-8"), name=str(path))


class Jf1VectorTests(unittest.TestCase):
    def test_frozen_vectors_against_one_fresh_mock_per_file(self) -> None:
        root = Path(__file__).resolve().parents[2]
        path = root / "vectors" / "protocol" / "jf1.vec"
        steps = load_vec(path)
        self.assertTrue(steps)
        transport = MockTransport()
        try:
            for step in steps:
                with self.subTest(path=path.name, line=step.line, request=step.request):
                    self.assertEqual(transport.write(step.request), len(step.request))
                    response = transport.readline()
                    self.assertTrue(match_response(step.expected, response),
                                    f"{path}:{step.line}: expected {step.expected!r}, got {response!r}")
                    self.assertEqual(transport.readline(), b"", "每个请求只能有一个应答")
                    self.assertNotIn("*", " ".join(step.expected), "JF1 没有易变字段")
        finally:
            transport.close()

    def test_deliberately_wrong_expected_response_fails(self) -> None:
        step, = parse_vec("> JF1 SET 0 255 0 255\n< JF1 OK SET 0 0 0 0\n")
        transport = MockTransport()
        try:
            transport.write(step.request)
            self.assertFalse(match_response(step.expected, transport.readline()))
        finally:
            transport.close()
