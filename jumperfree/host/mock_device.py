"""JF1/JF2 内存设备模型；未经硬件验证，线序只记录逻辑 BBM。"""

from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass
from typing import Callable

from ..core import JfError
from ..core.shadow import Shadow, apply_ops, shadow_bytes
from .errors import HostError
from .jf2 import commit_crc, encode_jf2, frame_tokens, parse_switch_op, uint
from .transport import MockTransport

LEASE_EXPIRED = 0x1
COMMIT_INTERRUPTED = 0x2
HW_FAULT = 0x4
WDT_RESET = 0x20
_CONFIG = {"brightness_cap": 64, "lease_default_ms": 0, "max_ops": 256}


@dataclass(frozen=True, slots=True)
class _Txn:
    id: str
    base: int
    n_ops: int
    frames: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class _Result:
    id: str
    state: str
    info: tuple[str, ...] = ()


class MockDevice(MockTransport):
    """Transport 与设备一体；clock 返回毫秒，seed 固定 boot 序列。"""

    def __init__(self, *, clock: Callable[[], float] | None = None, seed: int = 0,
                 reset_cause: str = "por") -> None:
        super().__init__()
        self._clock = clock if clock is not None else lambda: time.monotonic() * 1000
        self._offset = 0
        self._rng = random.Random(seed)
        self.hw_source = False
        self.reboot(reset_cause)

    def _now(self) -> float:
        return self._clock() + self._offset

    def reboot(self, cause: str = "por") -> None:
        if cause not in {"por", "iwdg", "sw"}:
            raise ValueError("invalid reset cause")
        self.boot = f"{self._rng.getrandbits(32):08x}"
        self.reset_cause = cause
        self._boot_time = self._now()
        self.state = "SAFE"
        self.shadow = Shadow()
        self.epoch = 0
        self.faults = (WDT_RESET if cause == "iwdg" else 0) | (HW_FAULT if self.hw_source else 0)
        self._hello = False
        self.lease_ms = _CONFIG["lease_default_ms"]
        self._lease_at = self._now()
        self._txn: _Txn | None = None
        self._results: list[_Result] = []
        self._interrupt = False
        self.execution_log: list[str] = []
        self.leds = [(0, 0, 0)] * 16
        self._responses.clear()
        self._buffer = b""

    def _remember(self, result: _Result) -> None:
        self._results = [r for r in self._results if r.id != result.id][-3:] + [result]

    def _result(self, id_: str) -> _Result | None:
        return next((r for r in self._results if r.id == id_), None)

    def _abort(self) -> None:
        if self._txn is not None:
            self._remember(_Result(self._txn.id, "ABORTED"))
            self._txn = None

    def _safe(self, fault: int = 0) -> None:
        self._abort()
        self.shadow = Shadow()
        self.epoch += 1
        self.state = "SAFE"
        self.faults |= fault
        self.execution_log = []

    def _expire(self) -> None:
        if self._hello and self.lease_ms and self.state != "SAFE" and self._now() - self._lease_at >= self.lease_ms:
            self._safe(LEASE_EXPIRED)

    def advance_ms(self, amount: int) -> None:
        if type(amount) is not int or amount < 0:
            raise ValueError("advance_ms requires nonnegative integer")
        self._offset += amount
        self._expire()

    def fault(self, source: str = "hw") -> None:
        if source != "hw":
            raise ValueError("unknown fault source")
        self.hw_source = True
        self._safe(HW_FAULT)

    def fault_source_gone(self, source: str = "hw") -> None:
        if source != "hw":
            raise ValueError("unknown fault source")
        self.hw_source = False

    def interrupt_next_commit(self) -> None:
        self._interrupt = True

    def directive(self, name: str, args: tuple[str, ...] = ()) -> None:
        """仅供已由 core.vectors 白名单校验的测试指令。"""
        if name == "advance_ms":
            self.advance_ms(int(args[0]))
        elif name == "fault":
            self.fault(args[0])
        elif name == "fault_source_gone":
            self.fault_source_gone(args[0])
        elif name == "reboot":
            self.reboot(args[0])
        elif name == "interrupt_next_commit":
            self.interrupt_next_commit()
        else:
            raise ValueError("unknown directive")

    def reset_input(self) -> None:
        if self.closed:
            raise OSError("mock transport is closed")
        self._responses.clear()

    def readline(self) -> bytes:
        self._expire()
        return super().readline()

    def _reply(self, line: bytes) -> bytes:
        self._expire()
        # 冻结向量含错误前缀 JF2 STATUS / CLR；这些旧命令名保留原 JF1 拒绝字节。
        if not line.startswith(b"JF2") or re.match(rb"JF2 +(?:SET|CLR|CLRALL|STATUS)(?:[ \r\n]|$)", line):
            return super()._reply(line)
        try:
            tokens = frame_tokens(line)
            if tokens[0] != "JF2" or len(tokens) < 2:
                raise HostError("BAD_FRAME", "invalid JF2 prefix or command")
            request = tokens[1:]
            encode_jf2(request)
            answer = self._dispatch(request)
            # 仅成功接受的 JF2 请求刷新；ERR 和 JF1 都不刷新。
            self._lease_at = self._now()
            body = "JF2 OK " + answer
        except JfError as exc:
            body = f"JF2 ERR {exc.code} {exc.message}"
        if self.faults and " f=" not in body:
            body += f" f={self.faults:x}"
        return (body + "\n").encode("ascii")

    @staticmethod
    def _error(code: str, message: str) -> None:
        raise HostError(code, message)

    def _info(self) -> str:
        return (f"fw=0.1.0 proto=2 caps=led,sw,txn,lease chips=2 board=v01 boot={self.boot} "
                f"epoch={self.epoch} state={self.state} reset={self.reset_cause}")

    def _dispatch(self, t: tuple[str, ...]) -> str:
        cmd, args = t[0], t[1:]
        if cmd == "HELLO":
            if uint(args[0]) != 2:
                self._error("UNSUPPORTED", "unsupported protocol")
            if not re.fullmatch(r"[0-9a-fA-F]{1,8}", args[1]):
                self._error("BAD_ARG", "invalid nonce")
            self._abort()
            self._hello = True
            self.state = "SAFE" if self.faults else "ARMED"
            return "HELLO " + self._info() + " nonce=" + args[1]
        if cmd == "INFO":
            return "INFO " + self._info() + " brightness_cap=64 lease_default_ms=0 max_ops=256"
        if cmd == "PING":
            return f"PING {args[0]} up={int(self._now() - self._boot_time)}"
        if cmd == "FAULT" and args[0] in {"GET", "CLEAR"}:
            if args[0] == "CLEAR":
                self.faults = HW_FAULT if self.hw_source else 0
            return f"FAULT {args[0]} f={self.faults:x}"
        if cmd == "SW":
            if args[0] == "ALLOPEN":
                self._safe()
                return "SW ALLOPEN"
            if args[0] == "STATUS":
                return (f"SW STATUS epoch={self.epoch} crc={self.shadow.shadow_crc():08x} "
                        f"c0={self.shadow.bitmap_hex(0)} c1={self.shadow.bitmap_hex(1)} state={self.state}")
            if args[0] == "TAGS":
                packed = shadow_bytes(self.shadow)[32:]
                return f"SW TAGS t0={packed[:32].hex()} t1={packed[64:96].hex()}"
        if cmd == "TXN" and args[0] == "QUERY":
            id_ = args[1]
            result = self._result(id_)
            state = "OPEN" if self._txn is not None and self._txn.id == id_ else (result.state if result else "UNKNOWN")
            info = " " + " ".join(result.info) if result and state == "COMMITTED" else ""
            return f"TXN QUERY {id_} result={state}" + info
        if cmd == "SELFTEST":
            level = uint(args[0])
            if level in (1, 2):
                self._error("UNSUPPORTED", "no sensing hardware")
            if level != 0:
                self._error("BAD_ARG", "invalid selftest level")
            if self.state == "STAGING":
                self._error("BUSY", "transaction open")
            return "SELFTEST 0 result=pass"
        if cmd == "CFG":
            if args[0] == "SET":
                self._error("UNSUPPORTED", "no maintenance mode")
            if args[0] == "GET":
                if args[1] not in _CONFIG:
                    self._error("BAD_ARG", "unknown configuration key")
                return f"CFG GET {args[1]} value={_CONFIG[args[1]]}"
        if cmd == "LEASE":
            self._require_session()
            value = uint(args[0])
            if value > 600000:
                self._error("BAD_ARG", "lease exceeds 600000 ms")
            self.lease_ms = value
            return "LEASE " + args[0]
        if cmd == "LED" and args[0] == "BATCH":
            self._require_session()
            if self.state == "STAGING":
                self._error("BUSY", "transaction open")
            count = uint(args[1])
            updates = args[2:]
            if count > 16 or len(updates) != count:
                self._error("BAD_ARG", "invalid LED count")
            parsed = []
            for value in updates:
                match = re.fullmatch(r"(0|[1-9][0-9]*):([0-9a-fA-F]{6})", value)
                if match is None or len(match[1]) > 2 or int(match[1]) > 15:
                    self._error("BAD_ARG", "invalid LED update")
                parsed.append((int(match[1]), tuple(bytes.fromhex(match[2]))))
            if len({clip for clip, _ in parsed}) != count:
                self._error("BAD_ARG", "duplicate LED clip")
            for clip, rgb in parsed:
                self.leds[clip] = rgb
            return "LED BATCH " + args[1]
        if cmd == "TXN" and args[0] in {"BEGIN", "ADD", "COMMIT", "ABORT"}:
            return self._transaction(args)
        self._error("UNSUPPORTED", "unknown command")

    def _require_session(self) -> None:
        if not self._hello or self.state == "SAFE":
            self._error("SAFE_MODE", "HELLO required without faults")

    def _transaction(self, args: tuple[str, ...]) -> str:
        sub, id_ = args[:2]
        # QUERY / 已完成 COMMIT / ABORT 重放在 SAFE 也可用。
        result = self._result(id_)
        if sub == "COMMIT" and result and result.state == "COMMITTED":
            return f"TXN COMMIT {id_} " + " ".join(result.info) + " replayed=1"
        if sub == "ABORT" and (self._txn is None or self._txn.id != id_):
            if result is None:
                self._require_session()
            if result and result.state == "COMMITTED":
                self._error("TXN_UNKNOWN", "transaction already committed")
            self._remember(_Result(id_, "ABORTED"))
            return f"TXN ABORT {id_}"
        self._require_session()
        if sub == "BEGIN":
            base, count = uint(args[2]), uint(args[3])
            if count > 256:
                self._error("TXN_TOO_LARGE", "max_ops is 256")
            if self._txn is not None:
                if self._txn.id != id_:
                    self._error("BUSY", "another transaction open")
                if (self._txn.base, self._txn.n_ops) != (base, count):
                    self._error("TXN_SEQ_CONFLICT", "BEGIN content changed")
                return f"TXN BEGIN {id_}"
            if result:
                self._error("TXN_UNKNOWN", "transaction id already finished")
            if base != self.epoch:
                self._error("EPOCH_MISMATCH", "base epoch differs")
            self._txn = _Txn(id_, base, count)
            self.state = "STAGING"
            return f"TXN BEGIN {id_}"
        txn = self._txn
        if txn is None or txn.id != id_:
            self._error("TXN_UNKNOWN", "transaction not open")
        if sub == "ABORT":
            self._abort()
            self.state = "ARMED"
            return f"TXN ABORT {id_}"
        if sub == "ADD":
            seq, content = uint(args[2]), args[3:]
            for token in content:
                parse_switch_op(token)
            if seq < len(txn.frames):
                if txn.frames[seq] != content:
                    self._error("TXN_SEQ_CONFLICT", "ADD content changed")
            elif seq != len(txn.frames):
                self._error("TXN_SEQ_GAP", "expected next sequence")
            else:
                if sum(map(len, txn.frames)) + len(content) > 256:
                    self._error("TXN_TOO_LARGE", "max_ops is 256")
                self._txn = _Txn(id_, txn.base, txn.n_ops, txn.frames + (content,))
            return f"TXN ADD {id_} {args[2]}"
        tokens = tuple(token for frame in txn.frames for token in frame)
        if not re.fullmatch(r"[0-9a-fA-F]{8}", args[3]):
            self._error("BAD_ARG", "CRC requires eight hex digits")
        if txn.base != self.epoch:
            self._error("EPOCH_MISMATCH", "base epoch differs")
        if (uint(args[2]) != len(txn.frames) or len(tokens) != txn.n_ops
                or int(args[3], 16) != commit_crc(tokens)):
            self._error("TXN_CRC", "frame count operation count or CRC differs")
        ops = tuple(parse_switch_op(token) for token in tokens)
        breaks, makes = tuple(op for op in ops if not op.on), tuple(op for op in ops if op.on)
        target = apply_ops(self.shadow, breaks, makes)
        self._safety(target)
        if self._interrupt:
            self._interrupt = False
            self._safe(COMMIT_INTERRUPTED)
            self._error("COMMIT_INTERRUPTED", "commit interrupted all open")
        self.execution_log = [token for sign in ("-", "+") for token in tokens if token.startswith(sign)]
        self.shadow = target
        self.epoch += 1
        self._txn = None
        self.state = "ARMED"
        info = (f"epoch={self.epoch}", f"crc={target.shadow_crc():08x}", f"opened={len(breaks)}", f"closed={len(makes)}")
        self._remember(_Result(id_, "COMMITTED", info))
        return f"TXN COMMIT {id_} " + " ".join(info)

    def _safety(self, target: Shadow) -> None:
        x_owner = {}
        y_owner = {}
        for chip in range(2):
            for x in range(8):
                for y in range(8):
                    bit = x * 8 + y
                    if target.closed[chip] & (1 << bit):
                        tag = target.tags[chip * 128 + bit]
                        if x_owner.get((chip, x), tag) != tag or y_owner.get(y, tag) != tag:
                            self._error("SAFETY_SHORT", "line has different tags")
                        x_owner[chip, x] = tag
                        y_owner[y] = tag
