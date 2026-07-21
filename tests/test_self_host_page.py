# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the download-first self-host landing (Program 9 W2).

The page inverts the on-ramp: self-host is the sovereign default, hosted is the
fallback. It leads with the one command, keeps the manual (Advanced) path, and
holds the Schweizer Hochdeutsch rules and the honesty framing.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
SELF_HOST_TEMPLATE = TEMPLATES / "self_host.html"

ONE_COMMAND = "curl -fsSL https://openleg.ch/install.sh | bash"


def _client():
    from flask import Flask

    import self_host

    app = Flask(__name__, template_folder=str(TEMPLATES))
    app.register_blueprint(self_host.self_host_bp)
    return app.test_client()


class TestSelfHostPage:
    def test_route_ok(self):
        assert _client().get("/self-host").status_code == 200

    def test_leads_with_one_command(self):
        html = _client().get("/self-host").data.decode("utf-8")
        assert ONE_COMMAND in html

    def test_quickstart_and_advanced_both_present(self):
        html = _client().get("/self-host").data.decode("utf-8")
        assert "Schnellstart" in html
        assert "Fortgeschritten" in html

    def test_sovereign_default_and_honesty(self):
        html = _client().get("/self-host").data.decode("utf-8")
        assert "Ihre Daten" in html or "Datenhoheit" in html

    def test_swiss_hochdeutsch_rules(self):
        html = SELF_HOST_TEMPLATE.read_text(encoding="utf-8")
        assert "ß" not in html
        assert "—" not in html  # em dash
        assert "–" not in html  # en dash

    def test_uses_shared_base(self):
        html = SELF_HOST_TEMPLATE.read_text(encoding="utf-8")
        assert 'extends "base.html"' in html


def test_sitemap_includes_self_host():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert '"/self-host"' in source


def test_funnel_links_to_self_host():
    for name in ("open_source.html", "leg_gruenden.html"):
        html = (TEMPLATES / name).read_text(encoding="utf-8")
        assert "/self-host" in html


def test_readme_reframes_self_host_as_default():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "most users do not self-host" not in readme.lower()
    assert "install.sh" in readme
