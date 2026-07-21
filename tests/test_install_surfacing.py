# SPDX-License-Identifier: AGPL-3.0-or-later
"""Website copy reflects the shipped self-host appliance (Program 9 W9).

The one-line installer is surfaced on the homepage and the open-source page, both
point to /self-host, and the updated pages stay in Schweizer Hochdeutsch.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
ONE_COMMAND = "curl -fsSL https://openleg.ch/install.sh | bash"


def _html(name):
    return (TEMPLATES / name).read_text(encoding="utf-8")


def test_homepage_surfaces_one_line_installer():
    html = _html("index.html")
    assert 'include "partials/install_console.html"' in html
    assert ONE_COMMAND in _html("partials/install_console.html")
    assert "/self-host" in html


def test_install_console_explains_effects_and_supports_copy():
    partial = _html("partials/install_console.html")
    assert "OpenLEG in einer Zeile selbst betreiben" in partial
    assert "erstellt eine lokale .env-Datei" in partial
    assert partial.count("data-install-tab") == 3
    assert partial.count("data-copy-command") == 3
    assert "Befehl kopieren" in partial

    homepage = _html("index.html")
    assert homepage.index('include "partials/install_console.html"') < homepage.index(
        'id="pfade"'
    )


def test_open_source_leads_with_one_line_installer():
    html = _html("open_source.html")
    assert ONE_COMMAND in html
    assert "/self-host" in html


def test_updated_pages_stay_swiss_hochdeutsch():
    for name in ("index.html", "open_source.html"):
        html = _html(name)
        assert "ß" not in html
        assert "—" not in html  # em dash
        assert "–" not in html  # en dash


def test_installer_command_color_is_compiled():
    # The dark code blocks use text-slate-100. If that class is missing from the
    # built CSS the command paints dark-on-dark and is invisible. This pins the
    # Tailwind rebuild so an invisible command can never ship again.
    css = (ROOT / "static" / "css" / "openleg.css").read_text(encoding="utf-8")
    assert "text-slate-100" in css


def test_nav_links_to_self_host():
    nav = (TEMPLATES / "partials" / "site_nav.html").read_text(encoding="utf-8")
    assert "/self-host" in nav
    assert "Selbst betreiben" in nav
