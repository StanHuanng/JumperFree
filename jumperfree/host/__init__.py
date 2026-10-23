"""Stage-1 Host: USB-UART framing for Clip0–15 RGB LEDs (hardware unverified)."""

from .errors import HostError
from .guide import GUIDE_PALETTE, plan_guide
from .protocol import Response
from .session import HostSession
from .transport import MockTransport, SerialTransport, Transport

__all__ = [
    "GUIDE_PALETTE", "HostError", "HostSession", "MockTransport", "Response",
    "SerialTransport", "Transport", "plan_guide",
]
