"""JF2 纯 ASCII 编解码；不依赖板级类型。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Iterable, Mapping

from ..core import crc32
from .errors import HostError

UINT = r"(?:0|[1-9][0-9]*)"
_MULTI = {"TXN", "SW", "LED", "FAULT", "CFG"}


def frame_tokens(line: bytes) -> tuple[str, ...]:
    """严格 LF、单空格、可打印 ASCII；JF2 不接受 CRLF。"""
    if not isinstance(line, bytes) or len(line) > 512 or not line.endswith(b"\n"):
        raise HostError("BAD_FRAME", "expected LF frame of at most 512 bytes")
    if not re.fullmatch(rb"[!-~]+(?: [!-~]+)*\n", line):
        raise HostError("BAD_FRAME", "expected printable ASCII and single spaces")
    return tuple(line[:-1].decode("ascii").split(" "))


def uint(token: str) -> int:
    """解析规范 uint32；格式和范围分别报告 BAD_FRAME / BAD_ARG。"""
    if not re.fullmatch(UINT, token):
        raise HostError("BAD_FRAME", "expected canonical unsigned integer")
    if len(token) > 10 or int(token) > 0xFFFFFFFF:
        raise HostError("BAD_ARG", "integer exceeds uint32")
    return int(token)


def encode_jf2(cmd_tokens: Iterable[str]) -> bytes:
    """编码不含 JF2 前缀的 tokens；十六进制 nonce/CRC 不按十进制检查。"""
    if isinstance(cmd_tokens, (str, bytes)):
        raise HostError("BAD_FRAME", "expected command tokens")
    tokens = tuple(cmd_tokens)
    if not tokens or any(not isinstance(t, str) or not re.fullmatch(r"[!-~]+", t) for t in tokens):
        raise HostError("BAD_FRAME", "expected nonempty printable tokens")
    frame = ("JF2 " + " ".join(tokens) + "\n").encode("ascii")
    frame_tokens(frame)
    # 这些字段是十进制；操作 token、nonce、CRC 和 RGB 是各自的独立语法。
    if tokens[0] in _MULTI and len(tokens) < 2:
        raise HostError("BAD_FRAME", "missing subcommand")
    head = tokens[:2]
    arity = {
        ("HELLO",): (3, 3), ("INFO",): (1, 1), ("PING",): (2, 2),
        ("LEASE",): (2, 2), ("SELFTEST",): (2, 2),
        ("TXN", "BEGIN"): (5, 5), ("TXN", "ADD"): (5, 512),
        ("TXN", "COMMIT"): (5, 5), ("TXN", "ABORT"): (3, 3),
        ("TXN", "QUERY"): (3, 3), ("SW", "STATUS"): (2, 2),
        ("SW", "TAGS"): (2, 2), ("SW", "ALLOPEN"): (2, 2),
        ("FAULT", "GET"): (2, 2), ("FAULT", "CLEAR"): (2, 2),
        ("LED", "BATCH"): (3, 512), ("CFG", "GET"): (3, 3),
        ("CFG", "SET"): (4, 4),
    }.get(head if tokens[0] in _MULTI else tokens[:1])
    if arity is not None and not arity[0] <= len(tokens) <= arity[1]:
        raise HostError("BAD_FRAME", "incorrect argument count")
    indices = {
        "HELLO": (1,), "PING": (1,), "LEASE": (1,), "SELFTEST": (1,),
    }.get(tokens[0], ())
    if head == ("TXN", "BEGIN"):
        indices = (2, 3, 4)
    elif head == ("TXN", "ADD"):
        indices = (2, 3)
    elif head == ("TXN", "COMMIT"):
        indices = (2, 3)
    elif head in (("TXN", "ABORT"), ("TXN", "QUERY"), ("LED", "BATCH")):
        indices = (2,)
    hex_indices = {2} if tokens[0] == "HELLO" else ({4} if head == ("TXN", "COMMIT") else set())
    for index, token in enumerate(tokens):
        if index in indices or (index not in hex_indices and re.fullmatch(r"[+-]?[0-9]+", token)):
            uint(token)
    return frame


@dataclass(frozen=True, slots=True)
class Crosspoint:
    chip: int
    x: int
    y: int


@dataclass(frozen=True, slots=True)
class SwitchOp:
    xp: Crosspoint
    on: bool
    tag: int = 0


def encode_switch_op(op: SwitchOp) -> str:
    """支持结构兼容的板级 SwitchOp；break 的内部 tag 必须为 0。"""
    if (any(type(v) is not int or not 0 <= v <= limit for v, limit in
            ((op.xp.chip, 1), (op.xp.x, 7), (op.xp.y, 7), (op.tag, 15)))
            or type(op.on) is not bool or (op.on and op.tag == 0) or (not op.on and op.tag != 0)):
        raise HostError("BAD_ARG", "invalid switch operation")
    return f"{'+' if op.on else '-'}{op.xp.chip}:{op.xp.x}:{op.xp.y}" + (f"@{op.tag}" if op.on else "")


def parse_switch_op(token: str) -> SwitchOp:
    match = re.fullmatch(r"([+-])([01]):([0-7]):([0-7])(?:@([1-9]|1[0-5]))?", token)
    if match is None or (match[1] == "+") != (match[5] is not None):
        raise HostError("BAD_ARG", "invalid switch operation")
    return SwitchOp(Crosspoint(int(match[2]), int(match[3]), int(match[4])), match[1] == "+", int(match[5] or 0))


def commit_crc(tokens: Iterable[str]) -> int:
    """按 seq 顺序，每个操作后一个 LF，zlib 兼容 CRC32。"""
    values = tuple(tokens)
    for token in values:
        parse_switch_op(token)
    return crc32("".join(token + "\n" for token in values).encode("ascii"))


@dataclass(frozen=True, slots=True)
class Jf2Response:
    ok: bool
    cmd: tuple[str, ...]
    args: tuple[str, ...] = ()
    kv: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    err_code: str | None = None
    message: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "kv", MappingProxyType(dict(self.kv)))


def parse_jf2_response(line: bytes) -> Jf2Response:
    """未知 key 原样保留；设备 ERR 与 JF1 一样抛 DEVICE_ERROR。"""
    try:
        tokens = frame_tokens(line)
        if len(tokens) < 3 or tokens[0] != "JF2":
            raise HostError("BAD_FRAME", "expected JF2 response")
        if tokens[1] == "ERR":
            if len(tokens) < 4 or not re.fullmatch(r"[A-Z][A-Z0-9_]*", tokens[2]):
                raise HostError("BAD_FRAME", "invalid device error")
            raise HostError("DEVICE_ERROR", " ".join(tokens[3:]), device_code=tokens[2])
        if tokens[1] != "OK" or not re.fullmatch(r"[A-Z]+", tokens[2]):
            raise HostError("BAD_FRAME", "expected OK command")
        end = 4 if tokens[2] in _MULTI else 3
        if len(tokens) < end or (end == 4 and not re.fullmatch(r"[A-Z]+", tokens[3])):
            raise HostError("BAD_FRAME", "missing subcommand")
        args = []
        kv = {}
        for token in tokens[end:]:
            if "=" in token:
                key, value = token.split("=", 1)
                if not re.fullmatch(r"[a-z][a-z0-9_]*", key) or not value or key in kv:
                    raise HostError("BAD_FRAME", "invalid response key/value")
                kv[key] = value
            else:
                if kv:
                    raise HostError("BAD_FRAME", "positional argument after key/value")
                args.append(token)
        return Jf2Response(True, tokens[2:end], tuple(args), kv)
    except HostError as exc:
        if exc.code == "DEVICE_ERROR":
            raise
        raise HostError("MALFORMED_RESPONSE", exc.message, reason=exc.code) from exc
