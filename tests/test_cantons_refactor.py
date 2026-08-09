# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contract tests for the shared Swiss canton constants."""

import ast
from pathlib import Path

import cantons
import municipality
import rangliste

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
}


def _python_files():
    for path in PROJECT_ROOT.rglob("*.py"):
        if SKIP_DIRS.intersection(path.relative_to(PROJECT_ROOT).parts):
            continue
        yield path


def test_cantons_exports_options_and_code_set():
    assert cantons.SWISS_CANTON_OPTIONS[0] == ("all", "Alle Kantone")
    assert cantons.SWISS_CANTON_OPTIONS[-1] == ("ZH", "Zürich")
    assert cantons.SWISS_CANTONS == {
        code for code, _ in cantons.SWISS_CANTON_OPTIONS if code != "all"
    }


def test_route_modules_use_shared_canton_options():
    assert municipality.SWISS_CANTON_OPTIONS is cantons.SWISS_CANTON_OPTIONS
    assert municipality.SWISS_CANTONS is cantons.SWISS_CANTONS
    assert rangliste.SWISS_CANTON_OPTIONS is cantons.SWISS_CANTON_OPTIONS


def test_no_module_imports_canton_options_from_municipality():
    offenders = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.module != "municipality":
                continue
            if any(alias.name == "SWISS_CANTON_OPTIONS" for alias in node.names):
                offenders.append(str(path.relative_to(PROJECT_ROOT)))

    assert offenders == []
