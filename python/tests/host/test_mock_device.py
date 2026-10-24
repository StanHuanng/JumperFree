from pathlib import Path
import random
import unittest

from jumperfree.core.shadow import Shadow
from jumperfree.core.vectors import VecDirective, match_response, parse_vec
from jumperfree.host.jf2 import commit_crc, parse_jf2_response
from jumperfree.host.mock_device import MockDevice

ROOT=Path(__file__).resolve().parents[3]


class MockDeviceTests(unittest.TestCase):
    def setUp(self):self.d=MockDevice(clock=lambda:0)
    def raw(self, command):
        self.d.write((command+'\n').encode('ascii'));return self.d.readline()
    def ok(self, command):return parse_jf2_response(self.raw('JF2 '+command))
    def err(self, command, code):
        line=self.raw('JF2 '+command)
        self.assertTrue(line.startswith(('JF2 ERR '+code+' ').encode()),line)
        return line
    def begin(self,id=1,n=1):self.ok(f'TXN BEGIN {id} {self.d.epoch} {n}')
    def add(self,ops,id=1,seq=0):self.ok(f'TXN ADD {id} {seq} '+' '.join(ops))
    def commit(self,ops,id=1,frames=1):return self.ok(f'TXN COMMIT {id} {frames} {commit_crc(ops):08x}')

    def test_all_static_vectors_including_frozen_jf1(self):
        for path in sorted((ROOT/'protocol/vectors').glob('*.vec')):
            self.d=MockDevice(clock=lambda:0)
            for step in parse_vec(path.read_text(),name=str(path)):
                with self.subTest(path=path.name,line=step.line):
                    if isinstance(step,VecDirective):self.d.directive(step.name,step.args)
                    else:
                        self.d.write(step.request)
                        response=self.d.readline()
                        self.assertTrue(match_response(step.expected,response),(step.expected,response))
                        self.assertEqual(self.d.readline(),b'')

    def test_boot_transport_injections_and_permissions(self):
        boot=self.d.boot
        self.d.reboot('sw');self.assertNotEqual(boot,self.d.boot)
        twin=MockDevice(clock=lambda:0);self.assertEqual(twin.boot,MockDevice(clock=lambda:0).boot)
        for fn,args in [(self.d.reboot,('bad',)),(self.d.advance_ms,(-1,)),(self.d.advance_ms,(True,)),
                        (self.d.fault,('bad',)),(self.d.fault_source_gone,('bad',)),(self.d.directive,('bad',))]:
            with self.assertRaises(ValueError):fn(*args)
        self.err('LEASE 10','SAFE_MODE');self.err('LED BATCH 0','SAFE_MODE');self.err('TXN ABORT 1','SAFE_MODE')
        for cmd in ['SW WHAT','FAULT WHAT','CFG WHAT','TXN WHAT','LED WHAT']:
            self.err(cmd,'UNSUPPORTED')
        self.err('SELFTEST 3','BAD_ARG');self.err('CFG GET absent','BAD_ARG')
        self.err('JF2','UNSUPPORTED')
        self.d.write(b'JF2x INFO\nJF2\n');self.assertIn(b'BAD_FRAME',self.d.readline());self.assertIn(b'BAD_FRAME',self.d.readline())
        self.ok('HELLO 2 1');self.err('LEASE 600001','BAD_ARG');self.ok('LEASE 600000')
        self.begin();self.err('SELFTEST 0','BUSY');self.err('LED BATCH 0','BUSY')
        self.err('TXN ADD 2 0 +0:0:0@1','TXN_UNKNOWN')
        self.ok('HELLO 2 2');self.assertEqual(self.ok('TXN QUERY 1').kv['result'],'ABORTED')
        self.d.write(b'JF1 SET 2 1');self.d.write(b' 2 3\n');self.d.reset_input();self.assertEqual(self.d.readline(),b'')
        self.d.close();self.d.close()
        for fn,args in [(self.d.write,(b'x',)),(self.d.readline,()),(self.d.reset_input,())]:
            with self.assertRaises(OSError):fn(*args)

    def test_bad_args_atomicity_bbm_and_cache_eviction(self):
        self.ok('HELLO 2 abc')
        for command in ['LED BATCH 17','LED BATCH 1','LED BATCH 1 bad','LED BATCH 1 00:ffffff',
                        'LED BATCH 1 999999:ffffff','LED BATCH 2 0:123456 0:654321']:
            self.err(command,'BAD_ARG');self.assertEqual(self.d.leds,[(0,0,0)]*16)
        self.begin();self.err('TXN ADD 1 0 +0:8:0@1','BAD_ARG')
        self.add(['-0:0:0']);before=self.d.shadow
        self.err(f'TXN COMMIT 1 1 {commit_crc(["-0:0:0"]):08x}','BAD_ARG');self.assertEqual(self.d.shadow,before)
        self.err('TXN COMMIT 1 1 zzzzzzzz','BAD_ARG')
        self.ok('TXN ABORT 1');self.ok('TXN ABORT 99');self.err('TXN BEGIN 1 0 0','TXN_UNKNOWN')
        for id in range(2,8):
            self.begin(id,0);self.commit([],id,0)
        self.assertEqual(self.ok('TXN QUERY 2').kv['result'],'UNKNOWN')
        self.err('TXN ABORT 7','TXN_UNKNOWN')
        self.begin(8);ops=['+0:0:0@1'];self.add(ops,8);self.commit(ops,8)
        self.begin(9);self.add(ops,9)
        self.err(f'TXN COMMIT 9 1 {commit_crc(ops):08x}','BAD_ARG');self.ok('TXN ABORT 9')
        self.begin(10,2);ops=['+0:0:1@2','-0:0:0'];self.add(ops,10);self.commit(ops,10)
        self.assertEqual(self.d.execution_log,['-0:0:0','+0:0:1@2'])
        self.begin(11);ops=['+0:0:2@3'];self.add(ops,11)
        self.err(f'TXN COMMIT 11 1 {commit_crc(ops):08x}','SAFETY_SHORT')
        self.assertEqual(self.d.execution_log,['-0:0:0','+0:0:1@2'])
        # Defensive COMMIT epoch check even if an external state mutation occurs.
        self.d.epoch+=1;self.err(f'TXN COMMIT 11 1 {commit_crc(ops):08x}','EPOCH_MISMATCH')
        self.ok('SW ALLOPEN');self.ok('HELLO 2 ff');self.begin(12,1);self.add(['+0:1:0@1'],12)
        for frames,crc in [(2,commit_crc(['+0:1:0@1'])),(1,0)]:
            self.err(f'TXN COMMIT 12 {frames} {crc:08x}','TXN_CRC')
        self.ok('TXN ABORT 12');self.begin(13,2);self.add(['+0:1:0@1'],13)
        self.err(f'TXN COMMIT 13 1 {commit_crc(["+0:1:0@1"]):08x}','TXN_CRC')

    def test_lease_refresh_bad_requests_and_hardware_latch(self):
        now=[0];self.d=MockDevice(clock=lambda:now[0]);self.ok('HELLO 2 a');self.ok('LEASE 10')
        self.begin();self.add(['+0:0:0@1']);self.commit(['+0:0:0@1'])
        now[0]=9;self.err('WHAT','UNSUPPORTED')
        now[0]=10;self.d.readline();self.assertEqual(self.d.shadow,Shadow());self.assertEqual(self.d.epoch,2)
        self.assertEqual(self.d.faults,1);self.ok('FAULT CLEAR');self.assertEqual(self.d.state,'SAFE')
        self.ok('HELLO 2 b');self.ok('LEASE 0');self.d.advance_ms(10**6);self.assertEqual(self.d.state,'ARMED')
        self.d.fault();self.d.reboot('por');self.assertEqual(self.d.faults,4)
        self.ok('FAULT CLEAR');self.assertEqual(self.d.faults,4)
        self.d.fault_source_gone();self.ok('FAULT CLEAR');self.assertEqual(self.d.state,'SAFE')

    def test_p4_random_bytes_never_mutate_shadow_or_crash(self):
        self.ok('HELLO 2 1');self.begin();self.add(['+0:0:0@1']);self.commit(['+0:0:0@1'])
        before=self.d.shadow;rng=random.Random(19)
        for _ in range(300):
            data=rng.randbytes(rng.randrange(1,700))
            self.d.write(data+b'\n');self.d.reset_input()
            self.assertEqual(self.d.shadow,before)

    def test_p5_dropped_repeated_add_and_reordering(self):
        rng=random.Random(8)
        for trial in range(20):
            self.d=MockDevice(clock=lambda:0);self.ok('HELLO 2 1')
            ops=[f'+{c}:{x}:0@1' for c in range(2) for x in range(4)]
            self.begin(n=len(ops))
            # A dropped ADD is retried with identical bytes; each ack may be duplicated.
            for seq,op in enumerate(ops):
                frame=f'JF2 TXN ADD 1 {seq} {op}\n'.encode()
                for _ in range(rng.randrange(1, 5)):
                    if rng.randrange(2): # Drop the other attempts before delivery.
                        self.d.write(frame)
                        self.d.reset_input() # Drop ACK too; next identical ADD must still succeed.
                self.d.write(frame);self.d.reset_input() # eventual delivery, lost ACK
                for _ in range(rng.randrange(1,4)):
                    self.d.write(frame);self.assertTrue(self.d.readline().startswith(b'JF2 OK TXN ADD'))
            self.commit(ops,frames=len(ops));self.assertEqual(self.d.shadow.closed,(0x1010101,0x1010101))
            self.begin(2,2);self.err('TXN ADD 2 1 +0:4:1@2','TXN_SEQ_GAP')

    def test_interrupted_commit_clears_existing_shadow_and_invalid_commit_keeps_injection(self):
        self.ok('HELLO 2 a');self.begin();ops=['+0:0:0@1'];self.add(ops);self.commit(ops)
        self.begin(2);ops=['+0:1:0@1'];self.add(ops,2);before=self.d.shadow
        self.d.interrupt_next_commit()
        self.err('TXN COMMIT 2 1 00000000','TXN_CRC')
        self.assertEqual(self.d.shadow,before)
        self.err(f'TXN COMMIT 2 1 {commit_crc(ops):08x}','COMMIT_INTERRUPTED')
        self.assertEqual((self.d.shadow,self.d.epoch,self.d.state,self.d.faults),(Shadow(),2,'SAFE',2))
        # Cached COMMIT replay is available in SAFE and never restores old connections.
        self.assertEqual(self.ok('TXN COMMIT 1 1 00000000').kv['replayed'],'1')
        self.assertEqual(self.d.shadow,Shadow())
        self.ok('TXN ABORT 2')
