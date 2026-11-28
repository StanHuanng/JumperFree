"""Normative switch shadow shared with the firmware model.

v01: 2 chips, 160 bytes. v04 (JumperFree mini): 7 chips, 560 bytes. Layout per
chip: 128-bit little-endian bitmap (bit = x*8+y); then 128 tag nibbles per chip.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol

from . import JfError, crc32


def _uint(value: object, limit: int, name: str) -> None:
    if type(value) is not int or not 0 <= value < limit:
        raise JfError("BAD_ARG", f"{name} must be an integer in 0..{limit - 1}")


@dataclass(frozen=True, slots=True)
class Shadow:
    """One 128-bit integer per chip and 128 unpacked tag values per chip (one byte each)."""

    closed: tuple[int, ...] = (0, 0)
    tags: bytes = bytes(256)

    def __post_init__(self) -> None:
        if type(self.closed) is not tuple or not 1 <= len(self.closed) <= 8:
            raise JfError("BAD_ARG", "closed must contain one 128-bit integer per chip (1..8)")
        for value in self.closed:
            _uint(value, 1 << 128, "bitmap")
        if (not isinstance(self.tags, bytes) or len(self.tags) != 128 * len(self.closed)
                or any(t > 15 for t in self.tags)):
            raise JfError("BAD_ARG", "tags must be 128 bytes per chip with values in 0..15")

    @classmethod
    def empty(cls, chips: int = 2) -> "Shadow":
        _uint(chips, 9, "chips")
        return cls((0,) * chips, bytes(128 * chips))

    @property
    def chips(self) -> int:
        return len(self.closed)

    def shadow_bytes(self) -> bytes:
        return shadow_bytes(self)

    def shadow_crc(self) -> int:
        return shadow_crc(self)

    def bitmap_hex(self, chip: int) -> str:
        return bitmap_hex(self, chip)


def shadow_bytes(shadow: Shadow) -> bytes:
    """Per-chip little-endian bitmaps, then even-low packed tag nibbles."""
    return b"".join(value.to_bytes(16, "little") for value in shadow.closed) + bytes(
        shadow.tags[k] | (shadow.tags[k + 1] << 4) for k in range(0, len(shadow.tags), 2)
    )


def shadow_crc(shadow: Shadow) -> int:
    return crc32(shadow_bytes(shadow))


def bitmap_hex(shadow: Shadow, chip: int) -> str:
    _uint(chip, len(shadow.closed), "chip")
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
            _uint(op.xp.chip, len(closed), "chip")
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
    return Shadow(tuple(closed), bytes(tags))
