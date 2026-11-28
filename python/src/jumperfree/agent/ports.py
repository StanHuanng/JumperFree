"""Find the JumperFree board among serial ports by asking each candidate for JF2 INFO."""

from __future__ import annotations

import importlib
import sys
from typing import Callable

from ..core.geometry import GEOMETRIES
from ..host import HostError
from ..host.jf2_session import Jf2Session
from ..host.transport import SerialTransport, Transport

# Only USB-UART bridges used on JumperFree boards are probed by default, so an
# auto search never writes to unrelated devices (an ESP32 can reset on open).
BRIDGE_VIDS = {0x1A86}  # WCH CH340 / CH343 family (JumperFree mini: CH340N)


def candidate_ports() -> list[str]:
    try:
        list_ports = importlib.import_module("serial.tools.list_ports")
    except ImportError as exc:
        raise HostError("SERIAL_UNAVAILABLE", "install pyserial (pip install jumperfree[host])") from exc
    ports = [port for port in list_ports.comports() if port.vid in BRIDGE_VIDS]
    return [port.device for port in sorted(ports, key=lambda port: port.device)]


def probe_info(transport: Transport, timeout: float = 0.5) -> dict[str, str]:
    """Send JF2 INFO once (any device state); return its key/values. No state change."""
    reply = Jf2Session(transport, frame_timeout=timeout, retries=1, max_reply=1024).request(("INFO",))
    return dict(reply.kv)


def open_board(port: str | None = None, *,
               serial_factory: Callable[..., Transport] = SerialTransport,
               ports: Callable[[], list[str]] = candidate_ports) -> tuple[Transport, str, dict[str, str]]:
    """Open ``port`` (or search) and return (transport, port, INFO key/values).

    A port that is open in another program reports PORT_BUSY; with no usable board
    the error is DEVICE_NOT_FOUND and lists what was tried.
    """
    names = [port] if port not in (None, "auto") else ports()
    tried: dict[str, str] = {}
    for name in names:
        try:
            transport = serial_factory(name)
        except HostError as exc:
            if port not in (None, "auto"):
                raise
            tried[name] = exc.code
            continue
        try:
            info = probe_info(transport)
            if info.get("board") in GEOMETRIES and info.get("proto") == "2":
                return transport, name, info
            tried[name] = "NOT_JUMPERFREE"
        except HostError as exc:
            tried[name] = exc.code
        try:
            transport.close()
        except (OSError, HostError):
            pass
        if port not in (None, "auto"):
            raise HostError("DEVICE_NOT_FOUND", "no JumperFree JF2 reply on this port", port=name,
                            reason=tried[name])
    if any(code == "PORT_BUSY" for code in tried.values()):
        raise HostError("PORT_BUSY", "the JumperFree port is open in another program "
                        "(another Host or a serial monitor); close it first", tried=tried)
    raise HostError("DEVICE_NOT_FOUND", "no JumperFree board found; plug in its USB-C cable",
                    tried=tried, platform=sys.platform)
