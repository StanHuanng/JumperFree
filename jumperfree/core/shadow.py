"""Normative 160-byte switch shadow shared with the future firmware model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol

from . import JfError, crc32


def _uint(value: object, limit: int, name: str) -> None:
    if type(value) is not int or not 0 <= value < limit:
        raise JfError("BAD_ARG", f"{name} must be an integer in 0..{limit - 1}")


@dataclass(frozen=True, slots=True)
class Shadow:
    """Two 128-bit integers and 256 unpacked tag values (one byte per value)."""

    closed: tuple[int, int] = (0, 0)
    tags: bytes = bytes(256)

    def __post_init__(self) -> None:
        if type(self.closed) is not tuple or len(self.closed) != 2:
            raise JfError("BAD_ARG", "closed must contain two 128-bit integers")
        for value in self.closed:
            _uint(value, 1 << 128, "bitmap")
        if not isinstance(self.tags, bytes) or len(self.tags) != 256 or any(t > 15 for t in self.tags):
            raise JfError("BAD_ARG", "tags must be 256 bytes with values in 0..15")

    def shadow_bytes(self) -> bytes:
        return shadow_bytes(self)

    def shadow_crc(self) -> int:
        return shadow_crc(self)

    def bitmap_hex(self, chip: int) -> str:
        return bitmap_hex(self, chip)


def shadow_bytes(shadow: Shadow) -> bytes:
    """Chip0, chip1 little-endian bitmaps, then even-low packed tag nibbles."""
    return b"".join(value.to_bytes(16, "little") for value in shadow.closed) + bytes(
        shadow.tags[k] | (shadow.tags[k + 1] << 4) for k in range(0, 256, 2)
    )


def shadow_crc(shadow: Shadow) -> int:
    return crc32(shadow_bytes(shadow))


def bitmap_hex(shadow: Shadow, chip: int) -> str:
    _uint(chip, 2, "chip")
    return shadow.closed[chip].to_bytes(16, "little").hex()


def parse_bitmap_hex(value: str) -> int:
    """Parse exactly 16 bytes in byte order; this is not integer hex order."""
    if not isinstance(value, str) or len(value) != 32 or any(c not in "0123456789abcdef" for c in value):
        raise JfError("BAD_ARG", "bitmap must be 32 lowercase hexadecimal characters")
    return int.from_bytes(bytes.fromhex(value), "little")


class CrosspointLike(Protocol):
    chip: int
    x: int
    y: int


class OpLike(Protocol):
    xp: CrosspointLike
    on: bool
    tag: int


def apply_ops(shadow: Shadow, breaks: Iterable[OpLike] = (), makes: Iterable[OpLike] = ()) -> Shadow:
    """Strict BBM replay. Breaks must be closed, makes open; break clears tag."""
    closed = list(shadow.closed)
    tags = bytearray(shadow.tags)
    for phase, operations in ((False, breaks), (True, makes)):
        for op in operations:
            _uint(op.xp.chip, 2, "chip")
            _uint(op.xp.x, 16, "x")
            _uint(op.xp.y, 8, "y")
            _uint(op.tag, 16, "tag")
            if type(op.on) is not bool or op.on != phase:
                raise JfError("BAD_ARG", "operation is in the wrong BBM phase")
            bit = op.xp.x * 8 + op.xp.y
            mask = 1 << bit
            if bool(closed[op.xp.chip] & mask) == phase:
                raise JfError("BAD_ARG", "break requires closed point; make requires open point")
            closed[op.xp.chip] ^= mask
            tags[op.xp.chip * 128 + bit] = op.tag if phase else 0
    return Shadow((closed[0], closed[1]), bytes(tags))
