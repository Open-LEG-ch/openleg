# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contracts for the public engineering and repository boundaries."""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENGINEERING_PATH = PROJECT_ROOT / "docs" / "engineering-contract.md"
PUBLIC_SITE_PATH = PROJECT_ROOT / "docs" / "public-site-release-contract.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _squash(text: str) -> str:
    return " ".join(text.split())


def test_public_engineering_contract_keeps_project_rules_discoverable():
    content = _read(ENGINEERING_PATH)
    for section in (
        "## Repository Boundary",
        "## Swiss German Text",
        "## Data Layer",
        "## Public Site and Frontend",
        "## Test Quality",
        "## CI and Branch Policy",
        "## Deployment Boundary",
        "## Data Policy",
    ):
        assert section in content

    for reference in (
        "CONTEXT.md",
        "docs/frontend-build.md",
        "docs/public-site-release-contract.md",
        "scripts/tdd_cycle.sh gate",
    ):
        assert reference in content


def test_public_docs_pin_site_ownership_and_release_safety():
    engineering = _read(ENGINEERING_PATH)
    release_contract = _read(PUBLIC_SITE_PATH)
    readme = _read(PROJECT_ROOT / "README.md")
    boundary = _read(PROJECT_ROOT / "docs" / "repo-boundary.md")
    registry = _read(PROJECT_ROOT / "docs" / "leg-registry.md")
    pipeline = _read(PROJECT_ROOT / "docs" / "data-pipeline.md")

    for content in (engineering, readme, boundary, registry):
        assert "public website" in content
        assert "openleg-ops" in content

    assert "anonymous `GET /`" in engineering
    assert "PUBLIC_SITE_URL" in engineering
    assert "at\n   most six requests" in release_contract
    assert "Do not crawl the full municipality directory" in release_contract

    public_docs = _squash(f"{readme}\n{boundary}\n{registry}\n{pipeline}")
    stale_claims = (
        "The marketing website runs separately from `openleg-ops`",
        "Put the marketing website runtime, public directories, ranking and legal pages in `openleg-ops`",
        "Die Marketing-Website läuft getrennt aus `openleg-ops`",
        "separately deployed website in `openleg-ops`",
        "separately deployed public website",
    )
    for claim in stale_claims:
        assert claim not in public_docs


def test_public_engineering_contract_stays_public_safe():
    content = _read(ENGINEERING_PATH)
    forbidden_patterns = (
        r"\b83\.228\.\d+\.\d+\b",
        r"ssh\s+-i\s+",
        r"docker compose exec openclaw",
        r"pairing required",
        r"OPENCLAW_GATEWAY_PASSWORD",
    )
    for pattern in forbidden_patterns:
        assert re.search(pattern, content, flags=re.IGNORECASE) is None, pattern


def test_public_data_policy_remains_explicit():
    content = _read(ENGINEERING_PATH)
    assert "Citizen smart meter data stays within each LEG" in content
    assert "Data is not sold" in content
    assert "share_with_neighbors" in content
