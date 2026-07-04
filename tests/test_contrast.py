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
    match = re.search(re.escape(selector) + r"\{([^}]*)\}", source)
    assert match, f"selector {selector!r} not found"
    prop_match = re.search(prop + r":\s*(#[0-9a-fA-F]{6})", match.group(1))
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


class TestFooterTextOnDark:
    """Footer separator (2.36:1) and EVU login link (3.75:1) on ink."""

    def test_no_underscaled_slate_on_dark_footer(self):
        footer = _read(os.path.join("templates", "partials", "site_footer.html"))
        assert "text-slate-600" not in footer, (
            "slate-600 is 2.36:1 on the ink footer; use slate-400 or lighter"
        )
        assert "text-slate-500" not in footer, (
            "slate-500 is 3.75:1 on the ink footer; use slate-400 or lighter"
        )


class TestDashboardDarkHero:
    """Dashboard hero: violet kicker/score/progress sat at 2.84:1 on ink."""

    @pytest.fixture(autouse=True)
    def load(self):
        self.css = _read(os.path.join("templates", "dashboard.html"))

    def test_default_kicker_readable_on_paper(self):
        color = _css_value(self.css, ".dashboard-kicker", "color")
        assert contrast(color, PAPER) >= 4.5, (
            f"page kicker {color} is {contrast(color, PAPER):.2f}:1 on paper"
        )

    def test_hero_kicker_readable_on_ink(self):
        color = _css_value(self.css, ".dashboard-hero .dashboard-kicker", "color")
        assert contrast(color, INK) >= 4.5, (
            f"hero kicker {color} is {contrast(color, INK):.2f}:1 on ink"
        )

    def test_score_readable(self):
        color = _css_value(self.css, ".dashboard-score", "color")
        assert contrast(color, INK) >= 3, (
            f"dashboard score {color} is {contrast(color, INK):.2f}:1 on ink "
            "(large text needs 3:1)"
        )

    def test_progress_fill_visible(self):
        color = _css_value(self.css, ".dashboard-progress span", "background")
        assert contrast(color, INK) >= 3, (
            f"progress fill {color} is {contrast(color, INK):.2f}:1 on ink "
            "(UI component needs 3:1)"
        )


class TestGrayMicrocopyOnPaper:
    """gray-400 (2.3-2.5:1) and gray-500 (4.4:1) fail on the paper body."""

    @pytest.mark.parametrize(
        "template",
        [
            os.path.join("templates", "gemeinde", "profil.html"),
            os.path.join("templates", "gemeinde", "verzeichnis.html"),
        ],
    )
    def test_no_gray_400_text(self, template):
        assert "text-gray-400" not in _read(template), (
            f"{template}: gray-400 is under 2.6:1 on white and paper"
        )

    @pytest.mark.parametrize(
        "template",
        [
            os.path.join("templates", "gemeinde", "profil.html"),
            os.path.join("templates", "gemeinde", "pilotgemeinde.html"),
        ],
    )
    def test_no_gray_500_text_on_paper_pages(self, template):
        assert "text-gray-500" not in _read(template), (
            f"{template}: gray-500 is 4.4:1 on the paper background "
            "(and slate-500 is 4.33:1); use text-ink-muted"
        )

    def test_savings_green_readable_on_paper(self):
        profil = _read(os.path.join("templates", "gemeinde", "profil.html"))
        assert "text-green-600" not in profil, (
            "green-600 is 3.0:1 on paper; use green-700"
        )


class TestKalkulatorDisabledButton:
    """Disabled submit was white text on slate-300 (1.48:1, unreadable)."""

    def test_disabled_state_overrides_text_color(self):
        html = _read(os.path.join("templates", "leg_kalkulator.html"))
        assert "disabled:text-" in html, (
            "the disabled Berechnen button keeps text-white on a light "
            "disabled background; add a disabled: text override"
        )


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
