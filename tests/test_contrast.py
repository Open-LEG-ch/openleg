# SPDX-License-Identifier: AGPL-3.0-or-later
"""WCAG AA contrast contracts for the violet brand on dark and paper
surfaces (issue #110).

A Chromium audit of the rendered public pages found the amber to violet
brand switch broke contrast on dark surfaces (footer wordmark, dashboard
hero) and left gray microcopy under threshold on the paper background.
These tests pin the fixed pairs with the WCAG relative-luminance math.
"""

import os
import re

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

INK = "#0f172a"
PAPER = "#f6f4ef"


def _read(rel_path):
    with open(os.path.join(PROJECT_ROOT, rel_path), encoding="utf-8") as handle:
        return handle.read()


def _luminance(hex_color):
    hex_color = hex_color.lstrip("#")
    channels = []
    for i in (0, 2, 4):
        value = int(hex_color[i : i + 2], 16) / 255
        channels.append(
            value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
        )
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg, bg):
    l1, l2 = _luminance(fg), _luminance(bg)
    return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)


def _css_value(source, selector, prop):
    # Whitespace-tolerant so behavior-preserving reformatting of the inline
    # CSS (spaces/newlines around "{", "color :", "color: #...") does not
    # break the contract.
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", source)
    assert match, f"selector {selector!r} not found"
    prop_match = re.search(
        re.escape(prop) + r"\s*:\s*(#[0-9a-fA-F]{6})", match.group(1)
    )
    assert prop_match, f"no {prop} hex on {selector!r}"
    return prop_match.group(1)


class TestInverseWordmarkOnDark:
    """Footer wordmark: 'LEG' and caret sat at 2.84:1 on the ink footer."""

    @pytest.fixture(autouse=True)
    def load(self):
        self.css = _read(os.path.join("templates", "partials", "brand_head.html"))

    def test_inverse_leg_readable_on_ink(self):
        color = _css_value(self.css, ".ol-logo--inverse .ol-leg", "color")
        assert contrast(color, INK) >= 4.5, (
            f"inverse wordmark LEG {color} is {contrast(color, INK):.2f}:1 on ink"
        )

    def test_inverse_caret_visible_on_ink(self):
        color = _css_value(self.css, ".ol-logo--inverse .ol-caret", "background")
        assert contrast(color, INK) >= 3, (
            f"inverse caret {color} is {contrast(color, INK):.2f}:1 on ink"
        )


class TestDashboardDarkHero:
    """Dashboard hero on ink: Tailwind utilities must keep WCAG contrast.

    The template now uses the shared brand build; these tests pin the
    utility-to-hex mapping so a class swap cannot silently fail contrast.
    """

    KICKER_ON_PAPER = "#1f3d32"  # text-brand (pine)
    PANEL = "#16302a"  # bg-brand-dark readiness panel
    HERO_KICKER_ON_PANEL = "#f0b968"  # text-accent-light (solar)
    SCORE_ON_PANEL = "#e8a13a"  # text-accent / bg-accent (solar)

    @pytest.fixture(autouse=True)
    def load(self):
        self.html = _read(os.path.join("templates", "dashboard.html"))

    def test_default_kicker_readable_on_paper(self):
        assert "text-brand" in self.html
        assert contrast(self.KICKER_ON_PAPER, PAPER) >= 4.5

    def test_hero_kicker_readable_on_ink(self):
        assert "text-accent-light" in self.html
        assert contrast(self.HERO_KICKER_ON_PANEL, self.PANEL) >= 4.5

    def test_score_readable(self):
        assert "text-accent " in self.html or 'text-accent"' in self.html
        assert contrast(self.SCORE_ON_PANEL, self.PANEL) >= 3, "large text needs 3:1"

    def test_progress_fill_visible(self):
        assert "bg-accent" in self.html
        assert contrast(self.SCORE_ON_PANEL, self.PANEL) >= 3, "UI component needs 3:1"


def test_replacement_pairs_actually_pass():
    """The chosen replacement colors must themselves clear WCAG AA."""
    indigo_400, indigo_300 = "#818cf8", "#a5b4fc"
    slate_400 = "#94a3b8"
    ink_muted, green_700 = "#475569", "#15803d"
    assert contrast(indigo_400, INK) >= 4.5
    assert contrast(indigo_300, INK) >= 4.5
    assert contrast(slate_400, INK) >= 4.5
    assert contrast(ink_muted, "#ffffff") >= 4.5
    assert contrast(ink_muted, PAPER) >= 4.5
    assert contrast(green_700, PAPER) >= 4.5
