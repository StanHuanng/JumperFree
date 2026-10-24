"""Enforce architecture dependencies using only the standard-library AST."""

from __future__ import annotations

import ast
from importlib.util import resolve_name
from pathlib import Path
import unittest


ALLOWED = {
    "core": {"core"},
    "routing": {"core", "routing"},
    "board": {"core", "routing", "board"},
    "host": {"core", "host"},
}


def forbidden_imports(source: str, module: str, *, is_package: bool = False) -> list[str]:
    """Return forbidden resolved imports, including ``from .. import board``."""
    parts = module.split(".")
    layer = parts[1] if len(parts) > 1 else ""
    package = module if is_package else module.rpartition(".")[0]
    failures = []
    for node in ast.walk(ast.parse(source)):
        targets = []
        if isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = resolve_name("." * node.level + base, package)
            targets = [base, *(f"{base}.{alias.name}" for alias in node.names)]
        for target in targets:
            imported = target.split(".")
            if len(imported) > 1 and imported[0] == "jumperfree":
                if layer in ALLOWED and imported[1] not in ALLOWED[layer]:
                    failures.append(f"{module}:{node.lineno}: {target}")
    return failures


class LayeringTests(unittest.TestCase):
    def test_package_dependencies(self) -> None:
        root = Path(__file__).resolve().parents[1] / "src"
        failures = []
        for path in sorted((root / "jumperfree").rglob("*.py")):
            parts = path.relative_to(root).with_suffix("").parts
            is_package = parts[-1] == "__init__"
            module = ".".join(parts[:-1] if is_package else parts)
            failures.extend(forbidden_imports(path.read_text(encoding="utf-8"), module,
                                              is_package=is_package))
        self.assertEqual(failures, [], "\n".join(failures))

    def test_checker_catches_absolute_and_relative_imports(self) -> None:
        for layer, target in (
            ("core", "routing"), ("core", "cli"),
            ("routing", "host"), ("routing", "service"),
            ("routing", "board"), ("routing", "device"),
            ("host", "routing"), ("host", "service"),
            ("host", "board"), ("host", "device"),
            ("board", "host"), ("board", "service"), ("board", "device"),
        ):
            for source in (
                f"import jumperfree.{target} as dependency",
                f"from jumperfree.{target} import symbol",
                f"from jumperfree import {target} as dependency",
                f"from ..{target} import symbol",
                f"from .. import {target} as dependency",
                f"from ..{target} import *",
            ):
                with self.subTest(layer=layer, source=source):
                    self.assertTrue(forbidden_imports(source, f"jumperfree.{layer}.example"))
        self.assertTrue(forbidden_imports("from ...host import HostSession",
                                          "jumperfree.routing.nested.example"))
        self.assertTrue(forbidden_imports("from ..service import guide", "jumperfree.host",
                                          is_package=True))

    def test_checker_allows_legal_imports(self) -> None:
        for module, source in (
            ("jumperfree.core.errors", "import json\nfrom . import JfError"),
            ("jumperfree.host.cli", "from ..core import canonical_json\nfrom .session import HostSession"),
            ("jumperfree.routing.model", "from jumperfree.core import JfError"),
            ("jumperfree.board.led", "from ..routing.model import RouteError\nfrom . import LedPlan"),
            ("jumperfree.service.guide", "from ..board import LedPlan\nfrom ..host import HostSession"),
            ("jumperfree.cli", "from .service import guide"),
            ("jumperfree", "from .service import guide"),
        ):
            with self.subTest(module=module):
                self.assertEqual(forbidden_imports(source, module, is_package=module == "jumperfree"), [])
