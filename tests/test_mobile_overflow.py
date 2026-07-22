# SPDX-License-Identifier: AGPL-3.0-or-later
"""Guards against horizontal page overflow at 360 pixels (issue #233).

A Chromium sweep measured `document.documentElement.scrollWidth` at 380 on the
homepage and 462 on the registry list, both against a 360 pixel client width.
The causes were a non-wrapping card header and an unbreakable heading, so these
tests pin the markup that keeps both contained.
"""

import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


def _read(name):
    return (TEMPLATES / name).read_text(encoding="utf-8")


def test_homepage_ranking_preview_cards_cannot_force_page_width():
    html = _read("index.html")
    cards = re.findall(
        r'<div class="[^"]*rounded-2xl border border-(?:emerald|rose)-200[^"]*"', html
    )
    assert len(cards) == 2
    for card in cards:
        assert "min-w-0" in card, card
    headers = re.findall(r'<div class="flex[^"]*items-center gap-2 mb-4">', html)
    assert headers
    for header in headers:
        assert "flex-wrap" in header, header


def test_registry_list_heading_wraps_on_narrow_viewports():
    heading = re.search(
        r"<h1 class=\"([^\"]+)\"[^>]*>", _read("leg_verzeichnis/liste.html")
    )
    assert heading, "registry list h1 not found"
    classes = heading.group(1)
    assert "break-words" in classes
    assert "text-3xl" in classes


def test_open_source_page_contains_its_grid_and_heading():
    """Measured 485 against a 360 client width before this guard (issue #233)."""
    html = _read("open_source.html")
    heading = re.search(r"<h1 class=\"([^\"]+)\"", html)
    assert heading
    assert "break-words" in heading.group(1)
    assert "text-3xl" in heading.group(1)
    sections = re.findall(r'<section class="grid md:grid-cols-2[^"]*"', html)
    assert sections
    for section in sections:
        assert "[&>div]:min-w-0" in section, section
