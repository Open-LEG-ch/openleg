# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared product-shell accessibility contracts."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_product_shell_has_one_focusable_main_without_public_navigation():
    shell = (ROOT / "templates/product_base.html").read_text()

    assert shell.count("<main") == 1
    assert 'id="main-content"' in shell
    assert 'tabindex="-1"' in shell
    assert "site_nav" not in shell
    assert "site_footer" not in shell


def test_source_and_compiled_css_have_global_focus_visible_treatment():
    source = (ROOT / "static/css/tailwind.css").read_text()
    compiled = (ROOT / "static/css/openleg.css").read_text()

    for css in (source, compiled):
        assert ":focus-visible" in css
