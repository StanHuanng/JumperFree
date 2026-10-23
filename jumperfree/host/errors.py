"""Structured failures for the Stage-1 LED host."""

from __future__ import annotations


class HostError(ValueError):
    """A host failure suitable for deterministic CLI output."""

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
