"""Deadline, stream framing and open-transaction bookkeeping regressions."""

import io
import unittest
from unittest import mock

from jumperfree.host.errors import HostError
from jumperfree.host.jf2_session import Jf2Session
from jumperfree.host.mock_device import MockDevice
from jumperfree.host.transport import SerialTransport
from tests.host.test_jf2_session import Clock, Scripted


class TrickleSerial(io.RawIOBase):
    """Each byte arrives before its timeout, without any wall-clock wait."""

    timeout = 1.0

    def __init__(self, clock):
        self.clock = clock
        self.timeouts = []

    def read(self, size=1):
        self.read_size = size
        self.timeouts.append(self.timeout)
        # A real monotonic clock has finite resolution and keeps advancing.
        self.clock.now += min(0.2, max(1e-9, self.timeout / 2))
        return b"x"

    def write(self, data):
        return len(data)

    def reset_input_buffer(self):
        pass


class ReadPathTests(unittest.TestCase):
    def test_trickling_serial_bytes_obey_absolute_deadline(self):
        clock = Clock()
        connection = TrickleSerial(clock)
        transport = SerialTransport.__new__(SerialTransport)
        transport._serial = connection
        with self.assertRaises(HostError) as raised:
            Jf2Session(transport, clock=clock, frame_timeout=.3, retries=0).request(['INFO'])
        self.assertEqual(raised.exception.code, 'TIMEOUT')
        self.assertAlmostEqual(clock.now, .3)
        self.assertLess(len(connection.timeouts), 100)
        self.assertEqual(connection.read_size, 1)
        self.assertEqual(connection.timeout, 1)
        self.assertTrue(all(0 < timeout <= .3 for timeout in connection.timeouts))
        self.assertLess(connection.timeouts[-1], connection.timeouts[0])

    def test_serial_empty_reads_size_bound_expiry_and_errors_restore_timeout(self):
        clock = Clock()
        connection = mock.Mock(timeout=.005)
        transport = SerialTransport.__new__(SerialTransport)
        transport._serial = connection
        connection.read.side_effect = [b'x'] * 513 + [b'']
        self.assertEqual(transport.readline_until(.3, clock=clock), b'x' * 513)
        self.assertEqual(connection.read.call_count, 513)
        self.assertEqual(transport.readline_until(.3, clock=clock), b'')
        self.assertEqual(connection.timeout, .005)
        before = connection.read.call_count
        self.assertEqual(transport.readline_until(0, clock=clock), b'')
        self.assertEqual(connection.read.call_count, before)
        for failure, code in [(OverflowError('too large'), 'INVALID_SERIAL_CONFIG'),
                              (OSError('lost'), 'TRANSPORT_ERROR')]:
            connection.read.side_effect = failure
            connection.write.side_effect = len
            with self.assertRaises(HostError) as raised:
                Jf2Session(transport, clock=clock).request(['INFO'])
            self.assertEqual(raised.exception.code, code)
            self.assertEqual(connection.timeout, .005)

    def test_split_ping_with_empty_partial_read(self):
        clock = Clock()
        transport = Scripted(clock, [[b'JF2 OK PI', b'', b'NG 1 up=0\n']])
        response = Jf2Session(transport, clock=clock, retries=0).request(['PING', '1'])
        self.assertEqual(response.kv['up'], '0')
        self.assertAlmostEqual(clock.now, .03)
        self.assertEqual(len(transport.writes), 1)

    def test_stale_frame_then_fragmented_matching_frame(self):
        clock = Clock()
        transport = Scripted(clock, [[b'JF2 OK PING 0 up=0\n', b'JF2 OK PI', b'NG 1 up=1\n']])
        response = Jf2Session(transport, clock=clock, retries=0).request(['PING', '1'])
        self.assertEqual(response.kv['up'], '1')
        self.assertAlmostEqual(clock.now, .03)

    def test_oversized_and_invalid_lines_discard_matching_suffix_until_lf(self):
        for prefix in [b'x' * 512, b'x' * 513, b'\x00', b'garbage ']:
            with self.subTest(prefix_length=len(prefix)):
                clock = Clock()
                transport = Scripted(clock, [[prefix, b'x', b'JF2 OK PING 1 up=bad\n',
                                              b'JF2 OK PI', b'NG 1 up=good\n']])
                response = Jf2Session(transport, clock=clock, retries=0).request(['PING', '1'])
                self.assertEqual(response.kv['up'], 'good')
                self.assertAlmostEqual(clock.now, .05)

    def test_frame_length_boundary_and_multiple_complete_lines_in_a_chunk(self):
        prefix = b'JF2 OK PING 1 pad='
        valid = prefix + b'x' * (511 - len(prefix)) + b'\n'
        for chunks in [[valid], [valid[:200], valid[200:]],
                       [valid[:-1] + b'x\n' + b'JF2 OK PING 0 up=0\n' + valid]]:
            with self.subTest(chunks=len(chunks)):
                clock = Clock()
                transport = Scripted(clock, [chunks])
                response = Jf2Session(transport, clock=clock, retries=0).request(['PING', '1'])
                self.assertEqual(len(response.kv['pad']), 511 - len(prefix))

    def test_retry_drops_partial_frame_from_previous_attempt(self):
        clock = Clock()
        transport = Scripted(clock, [[b'JF2 OK PING 1 up='],
                                     [b'bad\n', b'JF2 OK PING 1 up=good\n']])
        response = Jf2Session(transport, clock=clock, frame_timeout=.05, retries=1).request(['PING', '1'])
        self.assertEqual(response.kv['up'], 'good')
        self.assertEqual(transport.writes, [b'JF2 PING 1\n'] * 2)

    def test_retry_without_reset_resynchronizes_old_unterminated_lines(self):
        for prefix in [b'garbage ', b'x' * 513]:
            clock = Clock()
            scripted = Scripted(clock, [[prefix], [b'JF2 OK PING 1 up=bad\n',
                                                  b'JF2 OK PING 1 up=good\n']])
            transport = mock.Mock(spec=['write', 'readline', 'close'])
            transport.write.side_effect = scripted.write
            transport.readline.side_effect = scripted.readline
            response = Jf2Session(transport, clock=clock, frame_timeout=.05, retries=1).request(['PING', '1'])
            self.assertEqual(response.kv['up'], 'good')

    def test_late_partial_read_without_reset_requires_resync_on_next_request(self):
        clock = Clock()
        scripted = Scripted(clock, [[b'garbage '], [b'JF2 OK PING 1 up=bad\n',
                                                 b'JF2 OK PING 1 up=good\n']])
        transport = mock.Mock(spec=['write', 'readline', 'close'])
        transport.write.side_effect = scripted.write
        transport.readline.side_effect = scripted.readline
        session = Jf2Session(transport, clock=clock, frame_timeout=.005, retries=0)
        with self.assertRaises(HostError) as raised:
            session.request(['PING', '1'])
        self.assertEqual(raised.exception.code, 'TIMEOUT')
        session.frame_timeout = .3
        self.assertEqual(session.request(['PING', '1']).kv['up'], 'good')

    def test_commit_recovery_assembles_fragmented_query(self):
        clock = Clock()
        transport = Scripted(clock, [[b'JF2 OK TXN BEGIN 4\n'], [],
                                     [b'JF2 OK TXN QUERY 3 result=UNKNOWN\n',
                                      b'JF2 OK TXN QU', b'ERY 4 result=COM', b'MITTED epoch=1 ',
                                      b'crc=00000000 opened=0 closed=0\n']])
        session = Jf2Session(transport, clock=clock, retries=0)
        session.request(['TXN', 'BEGIN', '4', '0', '0'])
        response = session.request(['TXN', 'COMMIT', '4', '0', '00000000'])
        self.assertEqual(response.cmd, ('TXN', 'COMMIT'))
        self.assertEqual(response.args, ('4',))
        self.assertEqual(response.kv['replayed'], '1')
        self.assertEqual(session._ops, {})
        self.assertEqual(transport.writes[1:], [b'JF2 TXN COMMIT 4 0 00000000\n', b'JF2 TXN QUERY 4\n'])


class BookkeepingTests(unittest.TestCase):
    def test_thousand_zero_op_transactions_and_explicit_replay(self):
        session = Jf2Session(MockDevice(clock=lambda: 0))
        session.request(['HELLO', '2', 'a'])
        for txn_id in range(1000):
            session.request(['TXN', 'BEGIN', str(txn_id), str(txn_id), '0'])
            self.assertEqual(session._ops, {str(txn_id): 0})
            session.request(['TXN', 'COMMIT', str(txn_id), '0', '00000000'])
            self.assertEqual(session._ops, {})
        replay = session.request(['TXN', 'COMMIT', '999', '0', '00000000'], n_ops=0)
        self.assertEqual(replay.kv['replayed'], '1')
        self.assertEqual(session._ops, {})

    def test_abort_hello_allopen_and_close_prune_only_after_success(self):
        for ending in [['TXN', 'ABORT', '1'], ['HELLO', '2', 'b'], ['SW', 'ALLOPEN'], None]:
            with self.subTest(ending=ending):
                session = Jf2Session(MockDevice(clock=lambda: 0))
                session.request(['HELLO', '2', 'a'])
                session.request(['TXN', 'BEGIN', '1', '0', '2'])
                with self.assertRaises(HostError):
                    session.request(['TXN', 'COMMIT', '1', '0', '00000000'])
                self.assertEqual(session._ops, {'1': 2})
                with self.assertRaises(HostError):
                    session.request(['HELLO', '3', 'a'])
                self.assertEqual(session._ops, {'1': 2})
                session.request(['TXN', 'ABORT', '2'])
                self.assertEqual(session._ops, {'1': 2})
                if ending is None:
                    session.close()
                else:
                    session.request(ending)
                self.assertEqual(session._ops, {})

    def test_aborted_commit_recovery_prunes_but_open_keeps_count(self):
        for state in ['ABORTED', 'OPEN']:
            clock = Clock()
            transport = Scripted(clock, [[b'JF2 OK TXN BEGIN 1\n'], [],
                                         [b'JF2 OK TXN QUERY 1 result=' + state.encode() + b'\n']])
            session = Jf2Session(transport, clock=clock, retries=0)
            session.request(['TXN', 'BEGIN', '1', '0', '2'])
            with self.assertRaises(HostError) as raised:
                session.request(['TXN', 'COMMIT', '1', '0', '00000000'])
            self.assertEqual(raised.exception.code, 'COMMIT_UNRESOLVED')
            self.assertEqual(session._ops, {} if state == 'ABORTED' else {'1': 2})
