from __future__ import annotations

import unittest
from unittest import mock

from jumperfree.host import HostError, HostSession, MockTransport, Response


class SessionTests(unittest.TestCase):
    def test_stateful_round_trips_and_exact_writes(self) -> None:
        transport = MockTransport()
        session = HostSession(transport)
        self.assertEqual(session.status(), Response("STATUS", (0, 0, 0) * 16))
        self.assertEqual(session.set(0, 255, 128, 0), Response("SET", (0, 255, 128, 0)))
        self.assertEqual(session.set(15, 1, 2, 3), Response("SET", (15, 1, 2, 3)))
        colors = (255, 128, 0) + (0, 0, 0) * 14 + (1, 2, 3)
        self.assertEqual(session.status().values, colors)
        self.assertEqual(session.clear(0), Response("CLR", (0,)))
        self.assertEqual(session.status().values, (0, 0, 0) * 15 + (1, 2, 3))
        self.assertEqual(session.clear_all(), Response("CLRALL"))
        self.assertEqual(session.status().values, (0, 0, 0) * 16)
        self.assertEqual(transport.writes, [
            b"JF1 STATUS\n", b"JF1 SET 0 255 128 0\n", b"JF1 SET 15 1 2 3\n",
            b"JF1 STATUS\n", b"JF1 CLR 0\n", b"JF1 STATUS\n", b"JF1 CLRALL\n", b"JF1 STATUS\n",
        ])
        session.close()
        self.assertTrue(transport.closed)

    def test_invalid_input_does_not_write_or_poison_session(self) -> None:
        transport = MockTransport()
        session = HostSession(transport)
        for args in ((-1, 0, 0, 0), (16, 0, 0, 0), (0, 256, 0, 0), (0, 0, True, 0)):
            with self.subTest(args=args), self.assertRaises(HostError):
                session.set(*args)
        with self.assertRaises(HostError):
            session.clear(16)
        self.assertEqual(transport.writes, [])
        self.assertEqual(session.clear_all(), Response("CLRALL"))
        session.close()

    def test_bad_transactions_fail_and_session_cannot_be_reused(self) -> None:
        cases = (
            (b"", "TIMEOUT"), (b"JF1 OK CLRALL", "MALFORMED_RESPONSE"),
            (b"garbage\n", "MALFORMED_RESPONSE"),
            (b"JF1 OK CLR 1\n", "UNEXPECTED_RESPONSE"),
            (b"JF1 OK SET 0 2 2 3\n", "UNEXPECTED_RESPONSE"),
            (b"JF1 ERR BUSY try later\n", "DEVICE_ERROR"),
        )
        for frame, code in cases:
            with self.subTest(code=code, frame=frame):
                transport = MockTransport([frame, b"JF1 OK SET 0 1 2 3\n"])
                session = HostSession(transport)
                with self.assertRaises(HostError) as raised:
                    session.set(0, 1, 2, 3)
                self.assertEqual(raised.exception.code, code)
                with self.assertRaises(HostError) as raised:
                    session.set(0, 1, 2, 3)
                self.assertEqual(raised.exception.code, "SESSION_CLOSED")
                self.assertEqual(len(transport.writes), 1)
                session.close()

    def test_short_write_never_reads_or_retries(self) -> None:
        transport = MockTransport()
        with (
            mock.patch.object(transport, "write", return_value=2),
            mock.patch.object(transport, "readline") as read,
        ):
            session = HostSession(transport)
            with self.assertRaises(HostError) as raised:
                session.clear_all()
            self.assertEqual(raised.exception.as_dict(), {
                "code": "SHORT_WRITE", "message": "transport did not write the entire request",
                "details": {"written": 2},
            })
            read.assert_not_called()
            session.close()

    def test_transport_read_and_write_errors(self) -> None:
        for method in ("write", "readline"):
            with self.subTest(method=method):
                transport = MockTransport()
                session = HostSession(transport)
                with mock.patch.object(transport, method, side_effect=OSError("disconnected")):
                    with self.assertRaises(HostError) as raised:
                        session.status()
                self.assertEqual(raised.exception.code, "TRANSPORT_ERROR")
                self.assertEqual(raised.exception.details, {"operation": "STATUS"})
                with self.assertRaises(HostError):
                    session.status()
                session.close()

    def test_close_is_idempotent_and_prevents_commands(self) -> None:
        transport = MockTransport()
        session = HostSession(transport)
        with mock.patch.object(transport, "close", wraps=transport.close) as close:
            session.close()
            session.close()
            close.assert_called_once_with()
        with self.assertRaises(HostError) as raised:
            session.clear_all()
        self.assertEqual(raised.exception.code, "SESSION_CLOSED")

    def test_close_error_is_structured_and_not_retried(self) -> None:
        transport = MockTransport()
        session = HostSession(transport)
        with mock.patch.object(transport, "close", side_effect=OSError("close failed")) as close:
            with self.assertRaises(HostError) as raised:
                session.close()
            self.assertEqual(raised.exception.code, "TRANSPORT_ERROR")
            self.assertEqual(raised.exception.details, {"operation": "close"})
            session.close()
            close.assert_called_once_with()
