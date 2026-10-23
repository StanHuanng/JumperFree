"""Stage-1 Host: USB-UART framing for Clip0–15 RGB LEDs (hardware unverified)."""

from .errors import HostError
from .protocol import Response
from .session import HostSession
from .transport import MockTransport, SerialTransport, Transport

__all__ = [
    "HostError", "HostSession", "MockTransport", "Response",
    "SerialTransport", "Transport",
]
