"""Shared routing error type."""

from __future__ import annotations

from ..core import JfError


class RouteError(JfError):
    """A structured routing failure that is safe to expose through the CLI."""
