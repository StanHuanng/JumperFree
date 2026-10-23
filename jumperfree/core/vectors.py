"""Pure, deterministic parser and token matcher for shared protocol vectors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from . import JfError


class VecError(JfError):
    """A vector syntax failure with its source name and physical line number."""

    def __init__(self, message: str, *, name: str, line: int) -> None:
        super().__init__("VEC_SYNTAX", f"{name}:{line}: {message}", name=name, line=line)


@dataclass(frozen=True)
class VecStep:
    """One LF-terminated request and its expected response tokens."""

    request: bytes
    expected: tuple[str, ...]
    line: int


def _payload(text: str, *, name: str, line: int) -> bytes:
    result = bytearray()
    index = 0
    escapes = {"\\": 92, "r": 13, "t": 9}
    while index < len(text):
        char = text[index]
        index += 1
        if char == "\\":
            if index == len(text):
                raise VecError("转义符后缺少字符", name=name, line=line)
            char = text[index]
            index += 1
            if char in escapes:
                result.append(escapes[char])
            elif char == "x":
                digits = text[index:index + 2]
                if len(digits) != 2 or any(c not in "0123456789abcdefABCDEF" for c in digits):
                    raise VecError("\\x 后必须有两位十六进制数字", name=name, line=line)
                result.append(int(digits, 16))
                index += 2
            else:
                raise VecError(f"未知转义 \\{char}", name=name, line=line)
        elif not 0x20 <= ord(char) <= 0x7E:
            raise VecError("载荷原文只能含可打印 ASCII；其他字节请转义", name=name, line=line)
        else:
            result.append(ord(char))
    if 10 in result:
        raise VecError("载荷不能内嵌 LF；每步只能有一行", name=name, line=line)
    return bytes(result)


def parse_vec(text: str, *, name: str = "<vec>") -> tuple[VecStep, ...]:
    """Parse vector text without file IO; reject all reserved directives in M1."""
    steps = []
    pending: tuple[bytes, int] | None = None
    for line, raw in enumerate(text.split("\n"), 1):
        raw = raw.removesuffix("\r")
        if not raw.strip(" \t") or raw.startswith("#"):
            continue
        if raw.startswith("@"):
            raise VecError(f"未知指令（M1 尚不支持指令）：{raw}", name=name, line=line)
        if not raw.startswith(("> ", "< ")):
            raise VecError("行必须以 '#', '> ', '< ' 或 '@ ' 开头", name=name, line=line)
        if raw.startswith("> "):
            if pending is not None:
                raise VecError("上一个请求缺少唯一的应答行", name=name, line=line)
            pending = (_payload(raw[2:], name=name, line=line) + b"\n", line)
        else:
            if pending is None:
                raise VecError("应答行之前缺少请求", name=name, line=line)
            payload = _payload(raw[2:], name=name, line=line)
            try:
                expected = tuple(token.decode("ascii") for token in payload.split())
            except UnicodeDecodeError as exc:
                raise VecError("预期应答必须是 ASCII", name=name, line=line) from exc
            if not expected:
                raise VecError("预期应答至少需要一个 token", name=name, line=line)
            steps.append(VecStep(pending[0], expected, pending[1]))
            pending = None
    if pending is not None:
        raise VecError("请求缺少唯一的应答行", name=name, line=pending[1])
    return tuple(steps)


def match_response(expected_tokens: Sequence[str], actual_line: bytes) -> bool:
    """Compare one ASCII response by tokens, supporting only * and key=*."""
    body = actual_line.removesuffix(b"\n")
    if b"\n" in body:
        return False
    try:
        actual_tokens = [token.decode("ascii") for token in body.split()]
    except UnicodeDecodeError:
        return False
    if len(expected_tokens) != len(actual_tokens):
        return False
    for expected, actual in zip(expected_tokens, actual_tokens):
        if expected == "*":
            continue
        if len(expected) > 2 and expected.endswith("=*"):
            prefix = expected[:-1]
            if not actual.startswith(prefix) or len(actual) == len(prefix):
                return False
        elif expected != actual:
            return False
    return True
