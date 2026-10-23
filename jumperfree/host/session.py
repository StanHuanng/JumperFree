"""Synchronous high-level Clip LED API with one outstanding command."""

from __future__ import annotations

from .errors import HostError
from .protocol import Response, encode_request, parse_response
from .transport import Transport


class HostSession:
    """Own a transport; discard the session after any transaction failure."""

    def __init__(self, transport: Transport) -> None:
        self.transport = transport
        self._closed = False
        self._failed = False

    def set(self, clip: int, r: int, g: int, b: int) -> Response:
        return self._exchange("SET", clip, r, g, b)

    def clear(self, clip: int) -> Response:
        return self._exchange("CLR", clip)

    def clear_all(self) -> Response:
        return self._exchange("CLRALL")

    def status(self) -> Response:
        return self._exchange("STATUS")

    def _exchange(self, command: str, *values: int) -> Response:
        if self._closed or self._failed:
            raise HostError("SESSION_CLOSED", "session is closed or failed; open a new session")
        frame = encode_request(command, *values)
        try:
            written = self.transport.write(frame)
            if written != len(frame):
                raise HostError(
                    "SHORT_WRITE", "transport did not write the entire request", written=written,
                )
            line = self.transport.readline()
            if line == b"":
                raise HostError("TIMEOUT", "no response before transport timeout")
            response = parse_response(line)
            if response.command != command or (command != "STATUS" and response.values != values):
                raise HostError("UNEXPECTED_RESPONSE", "ACK does not match the request")
            return response
        except OSError as exc:
            self._failed = True
            raise HostError("TRANSPORT_ERROR", str(exc), operation=command) from exc
        except HostError:
            self._failed = True
            raise

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self.transport.close()
            except OSError as exc:
                raise HostError("TRANSPORT_ERROR", str(exc), operation="close") from exc
