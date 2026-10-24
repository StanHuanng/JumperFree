"""Structured failures for the Stage-1 LED host."""

from ..core import JfError


class HostError(JfError):
    """A host failure suitable for deterministic CLI output."""
