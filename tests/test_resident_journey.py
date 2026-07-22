# SPDX-License-Identifier: AGPL-3.0-or-later
"""Rendered resident-journey contracts for issue #214."""

import re
import importlib
import os
from html.parser import HTMLParser
from unittest.mock import MagicMock, patch

import pytest


class FormAuditParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.controls = set()
        self.focusable_controls = set()
        self.labels = set()
        self.ids = set()
        self.alerts = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if element_id := attrs.get("id"):
            self.ids.add(element_id)
            if attrs.get("role") == "alert" or "aria-live" in attrs:
                self.alerts.add(element_id)
        if tag in {"input", "select", "textarea"} and attrs.get("type") != "hidden":
            self.controls.add(attrs.get("id"))
            if "focus" in attrs.get("class", ""):
                self.focusable_controls.add(attrs.get("id"))
        if tag == "label":
            self.labels.add(attrs.get("for"))


@pytest.fixture
def resident_client():
    with patch.dict(
        os.environ,
        {
            "DATABASE_URL": "postgresql://x:x@localhost/x",
            "REDIS_URL": "memory://",
            "APP_BASE_URL": "http://localhost:5003",
        },
    ):
        with (
            patch("database.is_db_available", return_value=True),
            patch("database._connection_pool", MagicMock()),
        ):
            import app as app_module

            app_module = importlib.reload(app_module)
            with patch.object(
                app_module.db, "get_stats", return_value={"total_buildings": 0}
            ):
                yield app_module.app.test_client()


@pytest.fixture
def rendered(resident_client):
    pages = {}
    for route in ("/", "/fuer-bewohner", "/how-it-works", "/leg-kalkulator"):
        response = resident_client.get(route)
        assert response.status_code == 200
        pages[route] = response.get_data(as_text=True)
    return pages


def test_home_keeps_hero_installer_order(rendered):
    html = rendered["/"]
    assert html.index("data-home-hero") < html.index("data-home-installer")
    assert html.index("data-home-installer") < html.index('id="pfade"')


def test_rendered_registration_keeps_every_script_selector(rendered):
    html = rendered["/"]
    script = html[html.index("// Map (Leaflet") :]
    referenced_ids = set(re.findall(r"getElementById\(['\"]([^'\"]+)", script))
    parser = FormAuditParser()
    parser.feed(html)
    assert referenced_ids <= parser.ids
    assert 'data-address-state="empty"' in html


@pytest.mark.parametrize("route", ["/", "/leg-kalkulator"])
def test_every_form_control_has_bound_label_and_announced_errors(rendered, route):
    parser = FormAuditParser()
    parser.feed(rendered[route])
    assert None not in parser.controls
    assert parser.controls <= parser.labels
    assert parser.controls <= parser.focusable_controls
    assert parser.alerts


@pytest.mark.parametrize("route", ["/fuer-bewohner", "/how-it-works"])
def test_resident_pages_render_three_numbered_journey_steps(rendered, route):
    html = rendered[route]
    assert html.count('data-journey-step="') == 3
    for number in (1, 2, 3):
        assert f'data-journey-step="{number}"' in html


@pytest.mark.parametrize("route", ["/fuer-bewohner", "/leg-kalkulator"])
def test_resident_proof_renders_shared_provenance(rendered, route):
    html = rendered[route]
    assert 'data-testid="data-provenance"' in html
    assert 'href="#data-provenance"' in html


def test_resident_routes_still_render_without_new_context(rendered):
    assert all("<!DOCTYPE html>" in html for html in rendered.values())
