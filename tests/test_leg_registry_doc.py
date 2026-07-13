# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contract tests for the durable LEG registry goal doc."""

from pathlib import Path


DOC_PATH = Path("docs/leg-registry.md")


def _doc_text() -> str:
    return DOC_PATH.read_text(encoding="utf-8")


def test_leg_registry_doc_exists() -> None:
    assert DOC_PATH.exists(), "docs/leg-registry.md must exist"


def test_leg_registry_doc_includes_required_sections() -> None:
    text = _doc_text()
    for section in (
        "## Goal",
        "## Why This Matters",
        "## Phase 0",
        "## Phase 1",
        "## Phase 2",
        "## Explicit Non-Goals",
        "## Related Docs",
    ):
        assert section in text, section


def test_leg_registry_doc_states_topology_honesty_boundary() -> None:
    text = _doc_text().lower()
    assert "netz-topologie" in text or "netztopologie" in text
