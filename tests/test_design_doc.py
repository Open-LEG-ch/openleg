# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contract tests keeping design.md in sync with the shipped brand tokens."""

import os
import re

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESIGN_PATH = os.path.join(PROJECT_ROOT, "design.md")
TAILWIND_CONFIG_PATH = os.path.join(PROJECT_ROOT, "tailwind.config.js")

LEGACY_AMBER_HEXES = ("#f59e0b", "#fbbf24", "#d97706", "#ffc043")


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _shipped_brand_hexes():
    """Extract the hex tokens from the colors block of tailwind.config.js."""
    config = _read(TAILWIND_CONFIG_PATH)
    colors_block = re.search(r"colors:\s*\{.*?\n\s{6}\}", config, flags=re.DOTALL)
    assert colors_block, "colors block not found in tailwind.config.js"
    hexes = set(re.findall(r"#[0-9a-fA-F]{6}", colors_block.group(0)))
    assert hexes, "no hex tokens found in tailwind.config.js colors block"
    return hexes


class TestDesignDocBrandSync:
    @pytest.fixture(autouse=True)
    def load_docs(self):
        self.design = _read(DESIGN_PATH)
        self.design_lower = self.design.lower()

    def test_documents_every_shipped_brand_token(self):
        for hex_token in sorted(_shipped_brand_hexes()):
            assert hex_token.lower() in self.design_lower, (
                f"design.md does not document shipped token {hex_token} "
                "from tailwind.config.js"
            )

    def test_no_legacy_amber_hex(self):
        for hex_token in LEGACY_AMBER_HEXES:
            assert hex_token not in self.design_lower, (
                f"design.md still contains legacy amber token {hex_token}"
            )

    def test_no_amber_brand_language(self):
        assert "amber" not in self.design_lower, (
            "design.md still describes the brand as amber"
        )

    def test_documents_white_text_on_brand_fills(self):
        assert "bg-brand text-white" in self.design, (
            "design.md must document the shipped primary button pattern "
            "(violet fills carry white text)"
        )

    def test_no_stale_ink_on_brand_rule(self):
        assert "bg-brand text-ink" not in self.design, (
            "design.md still documents the old ink-text-on-brand button rule"
        )
