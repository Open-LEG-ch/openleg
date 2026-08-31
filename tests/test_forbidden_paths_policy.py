# SPDX-License-Identifier: AGPL-3.0-or-later
"""Policy tests for .github/forbidden-paths.txt."""

import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POLICY_PATH = os.path.join(PROJECT_ROOT, ".github", "forbidden-paths.txt")


def _patterns():
    with open(POLICY_PATH, encoding="utf-8") as handle:
        lines = []
        for raw in handle:
            item = raw.strip()
            if not item or item.startswith("#"):
                continue
            lines.append(item)
        return lines


def test_policy_includes_private_only_patterns():
    patterns = set(_patterns())
    required = {
        "archive/**",
        ".github/scripts/**",
        ".orch/**",
        "docs/research.md",
        "docs/*strategy*.md",
        "docs/*internal*.md",
        "grants/**",
        "internal/**",
        "overnight/**",
        "openclaw/**",
        "prd/**",
        "research*.md",
        "private/**",
        "strategy/**",
        "outreach/**",
        "workspace/**",
        "deploy.sh",
        ".env",
        "*.pem",
        "*.key",
        "*.crt",
    }
    missing = required - patterns
    assert not missing, f"Missing patterns: {sorted(missing)}"


def test_policy_blocks_local_agent_contracts():
    patterns = set(_patterns())
    assert "AGENTS.md" in patterns
    assert "CLAUDE.md" in patterns
