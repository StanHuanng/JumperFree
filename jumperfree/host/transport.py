"""Injectable byte transports; pyserial is imported only when explicitly used."""

from __future__ import annotations

import importlib
import math
from collections import deque
from typing import Iterable, Protocol

from .errors import HostError
from .protocol import CLIP_COUNT, MAX_LINE_BYTES, parse_request


class Transport(Protocol):
    def write(self, data: bytes) -> int:
        """Return the number of bytes written, or raise OSError."""

    def readline(self) -> bytes:
        """Return one bounded line, a partial line, or b'' on timeout."""

    def close(self) -> None:
        """Release transport resources, or raise OSError."""


class MockTransport:
    """In-memory LED peer; explicit responses replace automatic replies for tests."""

    def __init__(self, responses: Iterable[bytes] | None = None) -> None:
        self.closed = False
        self.writes: list[bytes] = []
        self.leds = [(0, 0, 0)] * CLIP_COUNT
        self._automatic = responses is None
        self._responses = deque(() if responses is None else responses)
        self._buffer = b""

    def write(self, data: bytes) -> int:
        if self.closed:
            raise OSError("mock transport is closed")
        self.writes.append(data)
        if self._automatic:
            self._buffer += data
            while b"\n" in self._buffer:
                line, _, self._buffer = self._buffer.partition(b"\n")
                self._responses.append(self._reply(line + b"\n"))
        return len(data)

    def _reply(self, line: bytes) -> bytes:
        try:
            command, values = parse_request(line)
            if command == "SET":
                self.leds[values[0]] = (values[1], values[2], values[3])
            elif command == "CLR":
                self.leds[values[0]] = (0, 0, 0)
            elif command == "CLRALL":
                self.leds = [(0, 0, 0)] * CLIP_COUNT
            else:
                values = tuple(channel for rgb in self.leds for channel in rgb)
            tokens = ("JF1", "OK", command, *(str(value) for value in values))
            return (" ".join(tokens) + "\n").encode("ascii")
        except HostError as exc:
            return f"JF1 ERR {exc.code} {exc.message}\n".encode("ascii")

    def readline(self) -> bytes:
        if self.closed:
            raise OSError("mock transport is closed")
        return self._responses.popleft() if self._responses else b""

    def close(self) -> None:
        self.closed = True


class SerialTransport:
    """Optional pyserial adapter with finite read/write timeouts and bounded reads."""

    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 1.0) -> None:
        try:
            valid_config = (
                isinstance(port, str) and bool(port.strip())
                and type(baudrate) is int and baudrate > 0
                and not isinstance(timeout, bool) and isinstance(timeout, (int, float))
                and math.isfinite(timeout) and timeout > 0
            )
        except OverflowError:
            valid_config = False
        if not valid_config:
            raise HostError(
                "INVALID_SERIAL_CONFIG",
                "port, positive baudrate and finite positive timeout required",
            )
        try:
            serial = importlib.import_module("serial")
        except ImportError as exc:
            raise HostError(
                "SERIAL_UNAVAILABLE", "install optional pyserial to use --port",
            ) from exc
        try:
            self._serial = serial.Serial(
                port=port, baudrate=baudrate, timeout=timeout, write_timeout=timeout,
            )
        except OverflowError as exc:
            raise HostError("INVALID_SERIAL_CONFIG", str(exc)) from exc
        except (OSError, ValueError) as exc:
            raise HostError("TRANSPORT_ERROR", str(exc), operation="open") from exc

    def write(self, data: bytes) -> int:
        return self._serial.write(data)

    def readline(self) -> bytes:
        return self._serial.readline(MAX_LINE_BYTES + 1)

    def close(self) -> None:
        self._serial.close()
