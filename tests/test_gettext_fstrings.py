# SPDX-License-Identifier: AGPL-3.0-or-later
"""Keep gettext extraction identical on Python 3.11 and 3.12."""

import ast
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GETTEXT_NAMES = {
    "_",
    "gettext",
    "ngettext",
    "pgettext",
    "npgettext",
    "dgettext",
    "dngettext",
    "dpgettext",
    "dnpgettext",
    "ugettext",
    "ungettext",
    "lazy_gettext",
    "gettext_noop",
    "_l",
    "_N",
    "recipient_gettext",
}


def gettext_in_fstrings(source):
    tree = ast.parse(source)
    names = GETTEXT_NAMES | {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
        if alias.name in GETTEXT_NAMES
    }
    return sorted(
        {
            call.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.JoinedStr)
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and (
                isinstance(call.func, ast.Name)
                and call.func.id in names
                or isinstance(call.func, ast.Attribute)
                and call.func.attr in names
            )
        }
    )


@pytest.mark.parametrize(
    "call",
    [
        "_('message')",
        "gettext('message')",
        "ngettext('one', 'many', count)",
        "flask_babel.gettext('message')",
        "escape(_('message'))",
        "recipient_gettext('message')",
        "translated('message')",
    ],
)
def test_guard_rejects_gettext_inside_fstrings(call):
    source = "from flask_babel import gettext as translated\n" + 'f"{' + call + '}"'
    assert gettext_in_fstrings(source) == [2]


def test_guard_checks_nested_format_specifications():
    assert gettext_in_fstrings("f\"{value:{_('width')}}\"") == [1]


def test_guard_allows_hoisted_calls_and_literal_examples():
    source = """label = gettext("message")
text = f"<p>{label}</p>"
example = "f'{gettext(message)}'"
"""
    assert gettext_in_fstrings(source) == []


def test_python_sources_keep_gettext_outside_fstrings():
    # Include new sources as well as tracked files, without walking virtualenvs.
    paths = (
        subprocess.check_output(
            [
                "git",
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
                "--",
                "*.py",
            ],
            cwd=ROOT,
        )
        .decode()
        .split("\0")
    )
    violations = []
    for filename in sorted(set(paths) - {""}):
        path = Path(filename)
        if any(
            part.startswith(".")
            or part
            in {
                "node_modules",
                "scratch",
                "work",
                "mutants",
            }
            for part in path.parts
        ):
            continue
        for line in gettext_in_fstrings((ROOT / path).read_text(encoding="utf-8")):
            violations.append(f"{filename}:{line}")
    assert not violations, "Hoist gettext calls out of f-strings: " + ", ".join(
        violations
    )
