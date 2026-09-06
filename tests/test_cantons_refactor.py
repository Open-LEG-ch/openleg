# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contract tests for the shared Swiss canton constants.

The module was created but never adopted: `api_public.py` kept its own literal
set of the same 26 codes, and the old test only forbade importing the options
from `municipality`, so nothing noticed. These tests pin the single source.
"""

import ast
from pathlib import Path

import api_public
import cantons

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".worktrees",
    "__pycache__",
    "archive",
    "node_modules",
    "private",
    "tests",
}


def _python_files():
    for path in PROJECT_ROOT.rglob("*.py"):
        if SKIP_DIRS.intersection(path.relative_to(PROJECT_ROOT).parts):
            continue
        yield path


def test_swiss_cantons_holds_all_twenty_six_codes():
    assert isinstance(cantons.SWISS_CANTONS, frozenset)
    assert len(cantons.SWISS_CANTONS) == 26
    assert {"AG", "GR", "JU", "TI", "VS", "ZH"} <= cantons.SWISS_CANTONS
    assert "all" not in cantons.SWISS_CANTONS


def test_api_public_reads_the_shared_set_rather_than_a_copy():
    assert api_public.SWISS_CANTONS is cantons.SWISS_CANTONS


def _bound_names(target):
    """Every name a target binds, including inside tuple and list unpacking."""
    if isinstance(target, ast.Name):
        yield target.id
    elif isinstance(target, ast.Starred):
        yield from _bound_names(target.value)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for element in target.elts:
            yield from _bound_names(element)


def test_no_other_module_defines_a_canton_constant():
    """Pasting the literal back into a consumer must turn this red."""
    offenders = []
    for path in _python_files():
        if path.name == "cantons.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(
                node,
                (ast.AnnAssign, ast.AugAssign, ast.For, ast.AsyncFor, ast.NamedExpr),
            ):
                targets = [node.target]
            for target in targets:
                for name in _bound_names(target):
                    if name.startswith("SWISS_CANTON"):
                        offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{name}")

    assert offenders == []


def test_the_unused_label_list_is_gone():
    """The code plus label pairs fed the public canton filter, now in the site repo."""
    assert not hasattr(cantons, "SWISS_CANTON_OPTIONS")
