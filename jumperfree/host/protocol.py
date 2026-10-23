"""JF1 ASCII framing; no firmware or switching implementation."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import HostError

CLIP_COUNT = 16
MAX_LINE_BYTES = 512
_ARITY = {"SET": 4, "CLR": 1, "CLRALL": 0, "STATUS": 0}


@dataclass(frozen=True)
class Response:
    """A validated ACK, or all 16 RGB triples for STATUS in clip order."""

    command: str
    values: tuple[int, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {"command": self.command, "values": list(self.values)}


def validate_request(command: str, values: tuple[int, ...]) -> None:
    if command not in _ARITY or len(values) != _ARITY[command]:
        raise HostError("INVALID_COMMAND", "unknown command or incorrect argument count")
    if command in ("SET", "CLR"):
        clip = values[0]
        if type(clip) is not int or not 0 <= clip < CLIP_COUNT:
            raise HostError("CLIP_OUT_OF_RANGE", "clip must be an integer in 0..15", clip=clip)
    if command == "SET":
        _validate_rgb(values[1:])


def _validate_rgb(values: tuple[int, ...]) -> None:
    if any(type(value) is not int or not 0 <= value <= 255 for value in values):
        raise HostError("INVALID_RGB", "RGB channels must be integers in 0..255")


def encode_request(command: str, *values: int) -> bytes:
    """Validate and encode one canonical LF-terminated request."""
    validate_request(command, values)
    return (" ".join(("JF1", command, *(str(value) for value in values))) + "\n").encode("ascii")


def _tokens(line: bytes) -> list[str]:
    if not isinstance(line, bytes) or len(line) > MAX_LINE_BYTES or not line.endswith(b"\n"):
        raise HostError("MALFORMED_FRAME", "expected a complete JF1 line of at most 512 bytes")
    try:
        body = line[:-1].removesuffix(b"\r").decode("ascii")
    except UnicodeDecodeError as exc:
        raise HostError("MALFORMED_FRAME", "JF1 frames must be ASCII") from exc
    if not re.fullmatch(r"[!-~]+(?: [!-~]+)*", body):
        raise HostError("MALFORMED_FRAME", "expected printable tokens separated by single spaces")
    tokens = body.split(" ")
    if tokens[0] != "JF1":
        raise HostError("MALFORMED_FRAME", "unsupported protocol version")
    return tokens


def _integers(tokens: list[str]) -> tuple[int, ...]:
    if any(not re.fullmatch(r"0|[1-9][0-9]*", token) for token in tokens):
        raise HostError("MALFORMED_FRAME", "expected canonical unsigned decimal integers")
    return tuple(int(token) for token in tokens)


def parse_request(line: bytes) -> tuple[str, tuple[int, ...]]:
    """Decode requests for the in-memory mock peer."""
    tokens = _tokens(line)
    if len(tokens) < 2:
        raise HostError("INVALID_COMMAND", "request is missing a command")
    command, values = tokens[1], _integers(tokens[2:])
    validate_request(command, values)
    return command, values


def parse_response(line: bytes) -> Response:
    """Parse an ACK or raise a structured device/protocol error."""
    try:
        tokens = _tokens(line)
        if len(tokens) >= 4 and tokens[1] == "ERR":
            if not re.fullmatch(r"[A-Z][A-Z0-9_]*", tokens[2]):
                raise HostError("MALFORMED_FRAME", "invalid device error code")
            raise HostError("DEVICE_ERROR", " ".join(tokens[3:]), device_code=tokens[2])
        if len(tokens) < 3 or tokens[1] != "OK":
            raise HostError("MALFORMED_FRAME", "expected OK command or ERR code message")
        command, values = tokens[2], _integers(tokens[3:])
        if command == "STATUS":
            if len(values) != CLIP_COUNT * 3:
                raise HostError("MALFORMED_FRAME", "STATUS must contain 16 RGB triples")
            _validate_rgb(values)
        else:
            validate_request(command, values)
        return Response(command, values)
    except HostError as exc:
        if exc.code == "DEVICE_ERROR":
            raise
        raise HostError("MALFORMED_RESPONSE", exc.message, reason=exc.code) from exc
