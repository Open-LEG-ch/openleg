# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contract tests for public-safe CLAUDE.md and AGENTS.md."""

import os
import re

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLAUDE_PATH = os.path.join(PROJECT_ROOT, "CLAUDE.md")
AGENTS_PATH = os.path.join(PROJECT_ROOT, "AGENTS.md")

pytestmark = pytest.mark.skipif(
    not os.path.exists(CLAUDE_PATH),
    reason="CLAUDE.md ist gitignored und fehlt in CI (nur lokal prüfen)",
)


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def test_agents_and_claude_are_byte_equal():
    assert _read(CLAUDE_PATH) == _read(AGENTS_PATH)


def test_docs_contain_boundary_sections():
    content = _read(CLAUDE_PATH)
    for section in ("## Repository Boundary", "## Public Repo", "## Private Ops Repo"):
        assert section in content


def test_docs_pin_public_site_ownership_and_entry_contract():
    content = _read(CLAUDE_PATH)
    release_contract = _read(
        os.path.join(PROJECT_ROOT, "docs", "public-site-release-contract.md")
    )

    assert "docs/public-site-release-contract.md" in content
    assert "anonymous `GET /` renders the public website" in content
    assert "assets live in this public app repository" in content
    assert "never copy those pages back" not in content
    assert "assets live in `openleg-ops`" not in content
    assert "at\n   most six requests" in release_contract
    assert "Do not crawl the full municipality directory" in release_contract


def test_docs_do_not_contain_private_ops_details():
    content = _read(CLAUDE_PATH)
    forbidden_patterns = [
        r"\b83\.228\.\d+\.\d+\b",
        r"ssh\s+-i\s+",
        r"docker compose exec openclaw",
        r"pairing required",
        r"OPENCLAW_GATEWAY_PASSWORD",
    ]
    for pattern in forbidden_patterns:
        assert re.search(pattern, content, flags=re.IGNORECASE) is None, pattern
