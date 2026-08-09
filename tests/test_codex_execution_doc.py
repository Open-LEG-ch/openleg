# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contract tests for the codex-execution review rubric."""

from pathlib import Path

DOC_PATH = Path("docs/codex-execution.md")


def _doc_text() -> str:
    return DOC_PATH.read_text(encoding="utf-8")


def test_codex_execution_doc_has_review_rubric_section() -> None:
    text = _doc_text()
    assert "## Review Rubric" in text


def test_codex_execution_doc_has_two_axis_review() -> None:
    text = _doc_text()
    assert "### Two axes: standards vs. spec" in text


def test_codex_execution_doc_has_deletion_test() -> None:
    text = _doc_text()
    assert "### The deletion test" in text
