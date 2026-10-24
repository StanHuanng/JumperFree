"""Pure shared formats and errors, independent of other JumperFree layers."""

from __future__ import annotations

import json
import zlib
from typing import Any, Mapping

SCHEMA = {
    "topology": "jf.topology/1", "state": "jf.routing-state/1",
    "plan": "jf.switch-plan/1", "calib": "jf.calib/1", "led": "jf.led-plan/1",
}


def canonical_json(obj: Any) -> str:
    """Serialize with the frozen CLI key ordering and whitespace conventions."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def crc32(data: bytes) -> int:
    """Return an unsigned, zlib-compatible CRC32."""
    return zlib.crc32(data) & 0xFFFFFFFF


def crc32_hex(data: bytes) -> str:
    """Return exactly eight lowercase hexadecimal CRC32 digits."""
    return f"{crc32(data):08x}"


class JfError(ValueError):
    """A structured failure preserving the existing host/routing contract."""

    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {"code": self.code, "message": self.message}
        if self.details:
            result["details"] = self.details
        return result


def error_envelope(
    code: str, layer: str, message: str, details: Mapping[str, object] | None = None,
    retryable: bool = False, state_changed: str = "no",
) -> dict[str, object]:
    """Build the §7.1 envelope for future commands; legacy CLI output is frozen."""
    if layer not in ("routing", "plan", "protocol", "device", "safety", "consistency"):
        raise ValueError("invalid error layer")
    if state_changed not in ("no", "yes", "unknown"):
        raise ValueError("invalid state_changed")
    return {"ok": False, "error": {
        "code": code, "layer": layer, "message": message,
        "details": {} if details is None else dict(details),
        "retryable": retryable, "state_changed": state_changed,
    }}
