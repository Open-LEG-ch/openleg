# SPDX-License-Identifier: AGPL-3.0-or-later
"""Accessible public shell contracts (issue #208).

Pins: one focusable <main> landmark owned by base.html, desktop nav links
gated to xl (mobile toggle stays available below xl), a shared footer with
contact/legal/methodology/provenance links, and a global :focus-visible
treatment for interactive controls.
"""

import glob
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = os.path.join(ROOT, "templates")


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


def _templates_extending_base():
    paths = []
    for path in glob.glob(os.path.join(TEMPLATES, "**", "*.html"), recursive=True):
        with open(path, encoding="utf-8") as handle:
            content = handle.read()
        if re.search(r'extends\s+["\']base\.html["\']', content):
            paths.append((os.path.relpath(path, TEMPLATES), content))
    return paths


class TestSingleMainLandmark:
    def test_base_html_wraps_block_content_in_focusable_main(self):
        base_html = _read("templates", "base.html")
        assert base_html.count("<main") == 1
        assert '<main id="main-content" tabindex="-1"' in base_html

        main_start = base_html.index("<main")
        block_start = base_html.index("{% block content %}")
        main_end = base_html.index("</main>")
        assert main_start < block_start < main_end

    def test_skip_link_targets_main_content(self):
        nav_html = _read("templates", "partials", "site_nav.html")
        assert 'href="#main-content"' in nav_html

    def test_main_content_id_is_not_duplicated_in_nav_partial(self):
        nav_html = _read("templates", "partials", "site_nav.html")
        assert 'id="main-content"' not in nav_html

    def test_templates_extending_base_do_not_nest_a_second_main(self):
        offenders = [
            name for name, content in _templates_extending_base() if "<main" in content
        ]
        assert offenders == [], f"nested <main> in templates extending base.html: {offenders}"


class TestNavBreakpoint:
    def test_desktop_links_appear_only_at_xl(self):
        nav_html = _read("templates", "partials", "site_nav.html")
        links_match = re.search(
            r'<div class="site-nav-links ([^"]+)"', nav_html
        )
        assert links_match, "expected site-nav-links container"
        classes = links_match.group(1)
        assert "xl:flex" in classes
        assert "md:flex" not in classes

    def test_mobile_toggle_stays_available_below_xl(self):
        nav_html = _read("templates", "partials", "site_nav.html")
        toggle_match = re.search(r'<button id="mobile-menu-toggle"[^>]*class="([^"]+)"', nav_html)
        assert toggle_match, "expected mobile menu toggle button"
        classes = toggle_match.group(1)
        assert "xl:hidden" in classes
        assert "md:hidden" not in classes

    def test_mobile_menu_panel_stays_below_xl(self):
        nav_html = _read("templates", "partials", "site_nav.html")
        menu_match = re.search(r'<div id="mobile-menu" class="([^"]+)"', nav_html)
        assert menu_match, "expected mobile menu panel"
        classes = menu_match.group(1)
        assert "xl:hidden" in classes
        assert "md:hidden" not in classes


class TestSharedFooter:
    def test_footer_exposes_required_links_and_provenance(self):
        footer_html = _read("templates", "partials", "site_footer.html")
        assert "mailto:{{ contact_email }}" in footer_html
        assert 'href="/impressum"' in footer_html
        assert 'href="/datenschutz"' in footer_html
        assert 'href="/rangliste/methodik"' in footer_html
        assert "github.com/Open-LEG-ch/openleg" in footer_html
        assert "AGPL-3.0-or-later" in footer_html

    def test_footer_wraps_links_in_a_flex_wrap_container(self):
        footer_html = _read("templates", "partials", "site_footer.html")
        assert "flex-wrap" in footer_html


class TestGlobalFocusVisible:
    def test_source_and_compiled_css_have_global_focus_visible_treatment(self):
        source_css = _read("static", "css", "tailwind.css")
        compiled_css = _read("static", "css", "openleg.css")
        assert ":focus-visible" in source_css
        assert ":focus-visible" in compiled_css


class TestInstallerTabSemantics:
    TAB_RE = re.compile(r'<button([^>]*data-install-tab="([^"]+)"[^>]*)>')
    PANEL_RE = re.compile(r'<div([^>]*data-install-panel="([^"]+)"[^>]*)>')

    def _attr(self, attrs, name):
        match = re.search(rf'{name}="([^"]*)"', attrs)
        return match.group(1) if match else None

    def _tabs(self, html):
        return [
            {
                "key": key,
                "id": self._attr(attrs, "id"),
                "tabindex": self._attr(attrs, "tabindex"),
                "selected": self._attr(attrs, "aria-selected"),
            }
            for attrs, key in self.TAB_RE.findall(html)
        ]

    def _panels(self, html):
        return [
            {
                "key": key,
                "id": self._attr(attrs, "id"),
                "labelledby": self._attr(attrs, "aria-labelledby"),
            }
            for attrs, key in self.PANEL_RE.findall(html)
        ]

    def test_tabs_have_stable_ids(self):
        html = _read("templates", "partials", "install_console.html")
        tabs = self._tabs(html)
        assert len(tabs) == 3
        assert all(tab["id"] for tab in tabs)
        assert len(set(tab["id"] for tab in tabs)) == 3

    def test_only_selected_tab_is_in_the_tab_order(self):
        html = _read("templates", "partials", "install_console.html")
        tabs = self._tabs(html)
        selected = [t for t in tabs if t["selected"] == "true"]
        unselected = [t for t in tabs if t["selected"] != "true"]
        assert len(selected) == 1
        assert selected[0]["tabindex"] == "0"
        assert unselected
        assert all(t["tabindex"] == "-1" for t in unselected)

    def test_panels_are_labelled_by_their_tab(self):
        html = _read("templates", "partials", "install_console.html")
        tabs = {t["key"]: t["id"] for t in self._tabs(html)}
        panels = self._panels(html)
        assert len(panels) == 3
        for panel in panels:
            assert panel["labelledby"] == tabs.get(panel["key"])

    def test_js_handles_arrow_and_home_end_keys_and_moves_focus(self):
        js = _read("static", "js", "install_console.js")
        assert "ArrowLeft" in js
        assert "ArrowRight" in js
        assert '"Home"' in js or "'Home'" in js
        assert '"End"' in js or "'End'" in js
        assert "tabindex" in js.lower()
        assert ".focus()" in js

    def test_js_wires_keydown_listener_and_prevents_default_scroll(self):
        js = _read("static", "js", "install_console.js")
        assert "addEventListener(\"keydown\"" in js or "addEventListener('keydown'" in js
        assert "preventDefault()" in js
