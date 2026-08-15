# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared quality contracts for the three stakeholder dashboards."""

import importlib
import os
import re
from unittest.mock import MagicMock, patch

import pytest

from tests.test_app_organic_routes import _disable_rate_limit_hooks


@pytest.fixture
def client():
    with (
        patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql://x:x@localhost/x",
                "REDIS_URL": "memory://",
                "CRON_SECRET": "test-cron-secret",
                "APP_BASE_URL": "http://localhost:5003",
            },
        ),
        patch("database.is_db_available", return_value=True),
        patch("database._connection_pool", MagicMock()),
    ):
        import app as imported_app

        imported_app = importlib.reload(imported_app)
        application = imported_app.create_app(load_environment=False)
        hooks = _disable_rate_limit_hooks(application)
        try:
            yield application.test_client()
        finally:
            application.before_request_funcs[None] = hooks


def _html(client, path):
    response = client.get(path)
    assert response.status_code == 200
    return response.get_data(as_text=True)


def test_demo_routes_keep_script_ids_and_context(client):
    resident = _html(client, "/dashboard/demo")
    municipality = _html(client, "/gemeinde/dashboard/demo")
    leg = _html(client, "/leg/dashboard/demo")

    for element_id in (
        "has-solar",
        "pv-field",
        "copy-ref",
        "ref-link",
        "calc-btn",
        "calc-error",
        "consumption",
        "pv-kwp",
        "savings-amount",
        "savings-result",
    ):
        assert f'id="{element_id}"' in resident
    for element_id in ("copy-invite", "invite-link"):
        assert f'id="{element_id}"' in municipality

    assert "Mellingerstrasse 12, 5400 Baden" in resident
    assert "Regionalwerke AG Baden" in municipality
    assert "Musterweg 1, 5400 Baden" in leg


def test_dashboards_keep_distinct_structures(client):
    pages = {
        "personal-readiness": _html(client, "/dashboard/demo"),
        "municipality-overview": _html(client, "/gemeinde/dashboard/demo"),
        "community-operations": _html(client, "/leg/dashboard/demo"),
    }
    for marker, html in pages.items():
        assert f'data-dashboard-structure="{marker}"' in html
        assert (
            sum(f'data-dashboard-structure="{other}"' in html for other in pages) == 1
        )


def test_dashboard_statistics_use_mono_tabular_numerals(client):
    for path in (
        "/dashboard/demo",
        "/gemeinde/dashboard/demo",
        "/leg/dashboard/demo",
    ):
        html = _html(client, path)
        statistics = re.findall(r'<[^>]+data-statistic(?!-)[^>]*class="([^"]+)"', html)
        assert statistics
        assert all(
            "font-mono" in classes and "tabular-nums" in classes
            for classes in statistics
        )
        assert "data-statistic-unit" in html


def test_dashboard_empty_states_are_actionable(client):
    municipality = _html(client, "/gemeinde/dashboard")
    leg = _html(client, "/leg/dashboard/demo")
    assert "Hier sehen Sie" in municipality
    assert 'href="/gemeinde/onboarding"' in municipality
    assert "Hier erscheinen" in leg
    assert "Vollzugriff im persönlichen Dashboard anfordern" in leg


def test_dashboard_errors_are_announced(client):
    for path in ("/dashboard", "/gemeinde/dashboard", "/leg/dashboard"):
        html = _html(client, path)
        assert re.search(r'<[^>]+role="alert"[^>]*>', html)
        assert "Öffnen" in html or "Prüfen" in html or "registrieren" in html


def test_readiness_score_explains_four_equal_checks(client):
    html = _html(client, "/dashboard/demo")
    assert "vier Checklisten-Punkte" in html
    assert "je ein Viertel" in html


def test_community_readiness_score_explains_weighted_steps(client):
    html = _html(client, "/leg/dashboard/demo")
    assert "Mindestens 3 Mitglieder bestätigen ihre Teilnahme" in html
    assert "Die LEG erstellt Dokumente oder sammelt Unterschriften" in html
    assert "Die LEG reicht die Anmeldung beim Netzbetreiber ein" in html
    assert "Der Netzbetreiber genehmigt sie" in html
    assert html.count("30 Prozentpunkte") == 2
    assert html.count("20 Prozentpunkte") == 2


def test_read_only_controls_describe_dashboard_access_request(client):
    html = _html(client, "/leg/dashboard/demo")
    labels = re.findall(r'<a href="/dashboard"[^>]*>(.*?)</a>', html, re.DOTALL)
    assert labels.count("Vollzugriff im persönlichen Dashboard anfordern") == 2
    assert "Eintrag hinzufügen" not in labels
    assert "Gründung starten" not in labels


def test_profilstatus_tile_is_removed(client):
    html = _html(client, "/dashboard/demo")
    assert "Profilstatus" not in html


def test_dashboard_form_controls_have_bound_labels(client):
    for path in (
        "/dashboard/demo",
        "/gemeinde/dashboard/demo",
        "/leg/dashboard/demo",
    ):
        html = _html(client, path)
        labels = set(re.findall(r'<label[^>]+for="([^"]+)"', html))
        controls = re.findall(r"<(?:input|select|textarea)\b[^>]*>", html)
        for control in controls:
            if 'type="hidden"' in control:
                continue
            match = re.search(r'\bid="([^"]+)"', control)
            assert match, control
            assert match.group(1) in labels, control


def test_dashboard_interactions_show_keyboard_focus(client):
    for path in (
        "/dashboard/demo",
        "/gemeinde/dashboard/demo",
        "/leg/dashboard/demo",
    ):
        html = _html(client, path)
        assert "data-dashboard-structure=" in html
        assert "focus-visible:outline" in html


def test_dark_panel_statistics_carry_light_text(client):
    """Statistics on the dark readiness panel must not inherit near-black ink.

    A browser check measured them at rgb(15, 23, 42) on the dark navy panel,
    which is effectively invisible. The panel is now warm pine (brand-dark);
    the statistics carry paper/white, never near-black ink.
    """
    html = _html(client, "/dashboard/demo")
    panel = re.search(
        r'aria-label="Kennzahlen".*?</div>\s*</div>\s*</div>', html, re.DOTALL
    )
    assert panel, "dark readiness panel with statistics not found"
    values = re.findall(r"<strong[^>]*font-mono[^>]*>", panel.group(0))
    assert len(values) == 2
    for value in values:
        assert "text-paper" in value or "text-white" in value, value
