import unittest
from unittest import mock

from jumperfree.host.errors import HostError
from jumperfree.host.jf2_session import Jf2Session
from jumperfree.host.mock_device import MockDevice
from jumperfree.host.transport import SerialTransport


class Clock:
    def __init__(self):self.now=0
    def __call__(self):return self.now


class Scripted:
    def __init__(self,clock,scripts):
        self.clock=clock;self.scripts=iter(scripts);self.queue=[];self.writes=[];self.resets=0;self.closed=False
    def reset_input(self):self.resets+=1;self.queue=[]
    def write(self,data):
        self.writes.append(data);script=next(self.scripts)
        if isinstance(script,Exception):raise script
        self.queue=list(script);return len(data)
    def readline(self):
        self.clock.now+=0.01
        return self.queue.pop(0) if self.queue else b''
    def close(self):self.closed=True


class SessionTests(unittest.TestCase):
    def error(self,code,fn,*args,**kw):
        with self.assertRaises(HostError) as ctx:fn(*args,**kw)
        self.assertEqual(ctx.exception.code,code);return ctx.exception

    def test_lost_ack_identical_retries_reset_and_matching(self):
        clock=Clock();t=Scripted(clock,[[],[b'JF1 OK CLRALL\n',b'garbage\n',b'JF2 OK INFO\n',
                                          b'JF2 OK PING 0 up=1\n',b'JF2 OK PING 1 2 up=1\n',b'JF2 OK PING 1 up=2\n']])
        s=Jf2Session(t,clock=clock,frame_timeout=.15)
        self.assertEqual(s.request(['PING','1']).kv['up'],'2')
        self.assertEqual(t.writes,[b'JF2 PING 1\n']*2);self.assertEqual(t.resets,2)
        # Duplicated and stale id/seq/nonce echoes are discarded.
        t=Scripted(clock,[[b'JF2 OK TXN ADD 0 0\n',b'JF2 OK TXN ADD 1 1\n',b'JF2 OK TXN ADD 1 0\n'],
                          [b'JF2 OK HELLO nonce=old\n',b'JF2 OK HELLO nonce=abc\n']])
        s=Jf2Session(t,clock=clock)
        self.assertEqual(s.request(['TXN','ADD','1','0','+0:0:0@1']).args,('1','0'))
        self.assertEqual(s.request(['HELLO','2','abc']).kv['nonce'],'abc')

    def test_endless_garbage_total_deadline_and_late_response(self):
        clock=Clock();t=Scripted(clock,[[b'garbage\n']*100]*3)
        s=Jf2Session(t,clock=clock,frame_timeout=.1)
        self.error('TIMEOUT',s.request,['PING','1'])
        self.assertLess(clock.now,.35);self.assertEqual(len(t.writes),3)
        clock=Clock();t=Scripted(clock,[[b'JF2 OK PING 1 up=0\n']])
        self.error('TIMEOUT',Jf2Session(t,clock=clock,frame_timeout=.005,retries=0).request,['PING','1'])

    def test_commit_timeout_queries_all_outcomes_and_no_resend(self):
        for state in ['COMMITTED','ABORTED','UNKNOWN','OPEN','BROKEN']:
            clock=Clock();extra=b' epoch=2 crc=12345678 opened=1 closed=2' if state=='COMMITTED' else b''
            t=Scripted(clock,[[],[b'JF2 OK TXN QUERY 4 result='+state.encode()+extra+b'\n']])
            s=Jf2Session(t,clock=clock,retries=0)
            if state=='COMMITTED':
                r=s.request(['TXN','COMMIT','4','1','12345678'],n_ops=3)
                self.assertEqual((r.cmd,r.args,r.kv['replayed']),(('TXN','COMMIT'),('4',),'1'))
                self.assertEqual(r.kv['epoch'],'2')
            else:
                e=self.error('COMMIT_UNRESOLVED',s.request,['TXN','COMMIT','4','1','12345678'],n_ops=3)
                self.assertEqual(e.details['state_changed'],'no' if state=='ABORTED' else 'unknown')
            self.assertEqual(t.writes,[b'JF2 TXN COMMIT 4 1 12345678\n',b'JF2 TXN QUERY 4\n'])
            self.assertGreaterEqual(clock.now,1.015)
        for response in [[],[b'JF2 ERR HW_FAULT failed\n'],OSError('lost')]:
            clock=Clock();t=Scripted(clock,[[],response]);s=Jf2Session(t,clock=clock,retries=0)
            code='TRANSPORT_ERROR' if isinstance(response,OSError) else ('DEVICE_ERROR' if response else 'TIMEOUT')
            e=self.error(code,s.request,['TXN','COMMIT','4','0','00000000'],n_ops=0)
            self.assertEqual(e.details['state_changed'],'unknown')
        clock=Clock();t=Scripted(clock,[[],[b'JF2 OK TXN QUERY 4 result=COMMITTED\n']])
        self.error('COMMIT_UNRESOLVED',Jf2Session(t,clock=clock,retries=0).request,
                   ['TXN','COMMIT','4','0','00000000'],n_ops=0)

    def test_errors_close_and_optional_transport_features(self):
        for kw in [{'frame_timeout':0},{'frame_timeout':float('nan')},{'frame_timeout':True},
                   {'frame_timeout':'x'},{'retries':-1},{'retries':True}]:
            self.error('BAD_ARG',Jf2Session,MockDevice(),**kw)
        self.error('BAD_FRAME',Jf2Session(MockDevice()).request,'PING 1')
        clock=Clock();t=Scripted(clock,[OSError('disconnect')]);s=Jf2Session(t,clock=clock)
        self.error('TRANSPORT_ERROR',s.request,['INFO'])
        t=Scripted(clock,[[b'JF2 ERR BAD_ARG rejected\n']]);s=Jf2Session(t,clock=clock)
        e=self.error('DEVICE_ERROR',s.request,['PING','1']);self.assertEqual(e.details['device_code'],'BAD_ARG')
        self.assertEqual(len(t.writes),1)
        self.error('BAD_ARG',s.request,['TXN','COMMIT','1','0','00000000'])
        with mock.patch.object(t,'write',return_value=0):self.error('SHORT_WRITE',s.request,['INFO'])
        s.close();s.close();self.assertTrue(t.closed);self.error('SESSION_CLOSED',s.request,['INFO'])
        t=Scripted(clock,[]);s=Jf2Session(t)
        with mock.patch.object(t,'close',side_effect=OSError('close')):self.error('TRANSPORT_ERROR',s.close)
        # Transport without reset_input still works.
        t=mock.Mock(spec=['write','readline','close']);t.write.side_effect=len
        t.readline.return_value=b'JF2 OK INFO\n';self.assertEqual(Jf2Session(t).request(['INFO']).cmd,('INFO',))
        # COMMIT device errors are never retried or followed by QUERY.
        clock=Clock();t=Scripted(clock,[[b'JF2 ERR TXN_CRC failed\n']]);s=Jf2Session(t,clock=clock)
        self.error('DEVICE_ERROR',s.request,['TXN','COMMIT','1','0','00000000'],n_ops=0)
        self.assertEqual(len(t.writes),1)

    def test_real_mock_session_and_expiry(self):
        device=MockDevice(clock=lambda:0);s=Jf2Session(device)
        s.request(['HELLO','2','a']);s.request(['LEASE','10']);s.request(['TXN','BEGIN','1','0','0'])
        self.assertEqual(s.request(['TXN','COMMIT','1','0','00000000']).kv['epoch'],'1')
        device.advance_ms(10);self.assertEqual(s.request(['SW','STATUS']).kv['state'],'SAFE')
        s.close()

    def test_serial_bounded_read_and_reset_preserve_jf1_read(self):
        serial=mock.Mock();connection=serial.Serial.return_value;connection.timeout=1
        connection.readline.return_value=b'JF2 OK INFO\n';connection.write.side_effect=len
        with mock.patch('jumperfree.host.transport.importlib.import_module',return_value=serial):
            transport=SerialTransport('port')
        s=Jf2Session(transport);s.request(['INFO'])
        connection.reset_input_buffer.assert_called_once_with();self.assertEqual(connection.timeout,1)
        connection.readline.side_effect=OSError('read')
        self.error('TRANSPORT_ERROR',s.request,['INFO']);self.assertEqual(connection.timeout,1)
