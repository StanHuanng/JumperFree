from dataclasses import FrozenInstanceError
import random
import unittest
import zlib

from jumperfree.host.errors import HostError
from jumperfree.host.jf2 import (Crosspoint, SwitchOp, Jf2Response, commit_crc, encode_jf2,
                                encode_switch_op, frame_tokens, parse_jf2_response, parse_switch_op)


class CodecTests(unittest.TestCase):
    def error(self, code, fn, *args):
        with self.assertRaises(HostError) as ctx:
            fn(*args)
        self.assertEqual(ctx.exception.code, code)
        return ctx.exception

    def test_frames(self):
        self.assertEqual(encode_jf2(['HELLO','2','000012ab']), b'JF2 HELLO 2 000012ab\n')
        self.assertEqual(len(encode_jf2(['X','a'*505])),512)
        for tokens in [[], 'PING 1', b'PING 1', ['X','a'*506], [''], ['X','a b'], ['X','é'], ['X',1],
                       ['PING'], ['SW'], ['TXN','BEGIN','1','0'], ['PING','01'], ['PING','-1'],
                       ['HELLO','02','a'], ['TXN','ADD','1','00','+0:0:0@1']]:
            self.error('BAD_FRAME',encode_jf2,tokens)
        for token in ['4294967296','9'*5000]:
            # Huge full frames hit the size check first.
            self.error('BAD_ARG' if len(token)<20 else 'BAD_FRAME',encode_jf2,['PING',token])
        for line in [None, b'x', b'x\r\n', b'x\x00\n', b' x\n', b'x  y\n', b'x\xff\n',b'x'*512+b'\n']:
            self.error('BAD_FRAME',frame_tokens,line)

    def test_response(self):
        r=parse_jf2_response(b'JF2 OK TXN ADD 5 2 extra=value\n')
        self.assertEqual((r.ok,r.cmd,r.args,dict(r.kv),r.err_code,r.message),
                         (True,('TXN','ADD'),('5','2'),{'extra':'value'},None,''))
        with self.assertRaises(TypeError):r.kv['x']='x'
        with self.assertRaises(FrozenInstanceError):r.ok=False
        self.assertEqual(parse_jf2_response(b'JF2 OK INFO strange=a=b\n').kv['strange'],'a=b')
        r=Jf2Response(True,('INFO',));self.assertEqual(dict(r.kv),{})
        for line in [b'JF1 OK INFO\n',b'JF2\n',b'JF2 ERR X\n',b'JF2 ERR bad msg\n',
                     b'JF2 NO INFO\n',b'JF2 OK info\n',b'JF2 OK TXN\n',b'JF2 OK TXN 5\n',
                     b'JF2 OK INFO k=\n',b'JF2 OK INFO K=a\n',b'JF2 OK INFO k=a k=b\n',
                     b'JF2 OK INFO k=a tail\n']:
            self.error('MALFORMED_RESPONSE',parse_jf2_response,line)
        e=self.error('DEVICE_ERROR',parse_jf2_response,b'JF2 ERR SAFE_MODE need hello\n')
        self.assertEqual(e.details,{'device_code':'SAFE_MODE'});self.assertEqual(e.message,'need hello')

    def test_operations_and_crc(self):
        rng=random.Random(42)
        for _ in range(200):
            xp=Crosspoint(rng.randrange(2),rng.randrange(8),rng.randrange(8))
            on=bool(rng.randrange(2));op=SwitchOp(xp,on,rng.randrange(1,16) if on else 0)
            token=encode_switch_op(op)
            self.assertEqual(parse_switch_op(token),op) # P4
            self.assertEqual(frame_tokens(encode_jf2(['TXN','ADD','1','0',token]))[5],token)
        for token in ['+0:0:0','-0:0:0@1','+0:0:0@0','+2:0:0@1','+0:8:0@1',
                      '+0:0:8@1','+0:0:0@16','+0:0:0@01','+00:0:0@1','bad']:
            self.error('BAD_ARG',parse_switch_op,token)
        for op in [SwitchOp(Crosspoint(2,0,0),True,1),SwitchOp(Crosspoint(0,0,0),True,0),
                   SwitchOp(Crosspoint(0,0,0),False,1),SwitchOp(Crosspoint(0,0,0),1,1)]:
            self.error('BAD_ARG',encode_switch_op,op)
        ops=['+0:3:2@5','+1:1:2@5']
        self.assertEqual(commit_crc(ops),zlib.crc32(b'+0:3:2@5\n+1:1:2@5\n'))
        self.assertEqual(commit_crc([]),0)
