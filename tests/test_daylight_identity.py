# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contract tests for the 'Daylight cooperative' visual identity.

Guards the move away from the cold dark-SaaS look (navy #070d1a, indigo neon
glow, gridline overlays) toward a warm communal palette: pine on paper with a
solar accent. Keeps the shipped tokens, the built CSS, design.md, and the hero
in sync.
"""

import glob
import hashlib
import os
import re

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TAILWIND_CONFIG = os.path.join(PROJECT_ROOT, "tailwind.config.js")
BUILT_CSS = os.path.join(PROJECT_ROOT, "static", "css", "openleg.css")
DESIGN_DOC = os.path.join(PROJECT_ROOT, "design.md")
FAVICON_SVG = os.path.join(PROJECT_ROOT, "static", "favicon.svg")
FAVICON_ICO = os.path.join(PROJECT_ROOT, "static", "favicon.ico")
APPLE_TOUCH_ICON = os.path.join(PROJECT_ROOT, "static", "apple-touch-icon.png")
BRAND_HEAD = os.path.join(PROJECT_ROOT, "templates", "partials", "brand_head.html")
TEMPLATE_GLOB = os.path.join(PROJECT_ROOT, "templates", "**", "*.html")

# The daylight-cooperative palette. brand == pine, accent == solar.
DAYLIGHT_TOKENS = (
    "#1f3d32",  # pine / brand
    "#e8a13a",  # solar / accent
    "#f5f2ea",  # paper
    "#22201b",  # ink
    "#6e8f7c",  # sage
)

# Cold neon-SaaS brand hexes that must not survive anywhere in source.
LEGACY_NEON = ("#4f46e5", "#6366f1", "#4338ca", "#070d1a", "#0f172a", "#111c31")


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def test_config_defines_daylight_tokens():
    config = _read(TAILWIND_CONFIG).lower()
    missing = [t for t in DAYLIGHT_TOKENS if t not in config]
    assert not missing, f"tailwind.config.js is missing daylight tokens: {missing}"


def test_config_has_no_legacy_neon_tokens():
    config = _read(TAILWIND_CONFIG).lower()
    offenders = [t for t in LEGACY_NEON if t in config]
    assert not offenders, (
        f"tailwind.config.js still carries legacy neon tokens: {offenders}"
    )


def test_built_css_is_rebuilt_with_pine():
    css = _read(BUILT_CSS).lower()
    assert "#1f3d32" in css, (
        "static/css/openleg.css was not rebuilt from the daylight tokens "
        "(pine #1f3d32 not found). Run the Tailwind build."
    )


def test_built_css_dropped_indigo_brand():
    css = _read(BUILT_CSS).lower()
    assert "#4f46e5" not in css, "built CSS still ships the indigo brand fill"


def test_design_doc_describes_daylight_identity():
    doc = _read(DESIGN_DOC).lower()
    for term in ("daylight", "pine", "solar", "paper"):
        assert term in doc, (
            f"design.md does not describe the '{term}' part of the identity"
        )


def test_favicon_uses_current_daylight_identity():
    svg = _read(FAVICON_SVG).lower()
    assert "#1f3d32" in svg
    assert "#e8a13a" in svg
    for legacy in LEGACY_NEON:
        assert legacy not in svg

    expected_hashes = {
        FAVICON_ICO: "fa7f2a09740f249ba27f2b5f578fa8e15c8343537846e72b1de0405d134dfa54",
        APPLE_TOUCH_ICON: "ac26c779cfb96af87a4f96fcfe4a43011badba6ec0ff967f0a3c1ea54a206ba1",
    }
    for path, expected_hash in expected_hashes.items():
        with open(path, "rb") as handle:
            assert hashlib.sha256(handle.read()).hexdigest() == expected_hash

    head = _read(BRAND_HEAD)
    assert "/static/favicon.svg?v=daylight" in head
    assert "/static/favicon.ico?v=daylight" in head
    assert "/static/apple-touch-icon.png?v=daylight" in head


def test_no_indigo_utility_classes_left_in_templates():
    offenders = []
    pattern = re.compile(r"\b(?:bg|text|border|from|to|via|ring|shadow)-indigo-\d+\b")
    for path in glob.glob(TEMPLATE_GLOB, recursive=True):
        for lineno, line in enumerate(_read(path).splitlines(), 1):
            if pattern.search(line):
                rel = os.path.relpath(path, PROJECT_ROOT)
                offenders.append(f"{rel}:{lineno}: {line.strip()[:100]}")
    assert not offenders, "indigo utility classes remain:\n" + "\n".join(offenders)
