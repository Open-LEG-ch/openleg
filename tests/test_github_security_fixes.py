# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression tests for GitHub security alerts (bleach, CodeQL).

These tests verify that the fixes do not expose internal exception details,
do not use unsafe regex on user input, and keep the JS template surface safe.
"""

import importlib
import os
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _disable_rate_limit_hooks(flask_app):
    hooks = list(flask_app.before_request_funcs.get(None, []))
    flask_app.before_request_funcs[None] = [
        hook
        for hook in hooks
        if not (
            getattr(hook, "__module__", "").startswith("flask_limiter")
            or getattr(hook, "__name__", "") == "_check_request_limit"
        )
    ]
    return hooks


@pytest.fixture
def full_app_module():
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
        import app as app_module

        app_module = importlib.reload(app_module)
        app_module.web = app_module.create_app(load_environment=False)
        hooks = _disable_rate_limit_hooks(app_module.web)
        try:
            yield app_module
        finally:
            app_module.web.before_request_funcs[None] = hooks


# ---------------------------------------------------------------------------
# Dependency / validation fixes
# ---------------------------------------------------------------------------


def test_validate_email_address_hides_exception_details():
    from security_utils import validate_email_address

    is_valid, normalized, error = validate_email_address("not-an-email")
    assert is_valid is False
    assert normalized is None
    assert error
    # The message must be generic; it must not echo the library exception text.
    assert "The email address is not valid" not in error
    assert "Email is not valid" not in error


def test_parse_ekz_csv_hides_exception_details():
    from meter_data import parse_ekz_csv

    csv_content = "Zeitstempel;Verbrauch (kWh);Produktion (kWh);Einspeisung (kWh)\n01.01.2026 00:15;abc;0;0\n"
    readings, errors = parse_ekz_csv(csv_content)
    assert readings == []
    assert errors
    assert not any("could not convert" in e for e in errors)
    assert not any("ValueError" in e for e in errors)


def test_parse_ckw_csv_hides_exception_details():
    from meter_data import _parse_ckw_csv

    csv_content = "Datum;Zeit;Bezug (kWh);Rücklieferung (kWh)\n01.01.2026;00:15;abc;0\n"
    readings, errors = _parse_ckw_csv(csv_content)
    assert readings == []
    assert errors
    assert not any("could not convert" in e for e in errors)
    assert not any("ValueError" in e for e in errors)


def test_get_energy_profile_for_address_strips_html_tags(monkeypatch):
    import data_enricher

    captured = {}

    def fake_get_coords(address_string):
        captured["address"] = address_string
        return None, None, None

    monkeypatch.setattr(data_enricher, "get_coordinates_from_address", fake_get_coords)

    data_enricher.get_energy_profile_for_address("<script>alert(1)</script>My Street 1")

    assert captured["address"] == "alert(1)My Street 1"


def test_get_energy_profile_for_address_tag_removal_is_linear(monkeypatch):
    import data_enricher

    monkeypatch.setattr(
        data_enricher, "get_coordinates_from_address", lambda _a: (None, None, None)
    )

    malicious = "<a" * 40000
    start = time.perf_counter()
    data_enricher.get_energy_profile_for_address(malicious)
    elapsed = time.perf_counter() - start

    # A linear pass should be essentially instant; polynomial backtracking is not.
    assert elapsed < 0.8


# ---------------------------------------------------------------------------
# Route fixes
# ---------------------------------------------------------------------------


def test_check_potential_does_not_expose_exception_text(full_app_module, monkeypatch):
    app_module = full_app_module
    monkeypatch.setattr(
        app_module,
        "find_provisional_matches",
        MagicMock(side_effect=RuntimeError("boom")),
    )

    client = app_module.web.test_client()
    resp = client.post(
        "/api/check_potential", json={"address": "Bahnhofstrasse 1, Zürich"}
    )

    assert resp.status_code == 500
    data = resp.get_json()
    assert "error" in data
    assert "boom" not in data["error"]


def test_meter_data_upload_malformed_tier_returns_json_error(full_app_module):
    app_module = full_app_module
    client = app_module.web.test_client()
    resp = client.post(
        "/api/meter-data/upload",
        json={"building_id": "b-123", "csv_content": "x", "tier": "not-an-int"},
    )

    assert resp.status_code in (400, 500)
    data = resp.get_json()
    assert data is not None
    assert "error" in data
    html = resp.get_data(as_text=True)
    assert "ValueError" not in html
    assert "could not convert" not in html
    assert "invalid literal" not in html


def test_meter_data_upload_get_building_error_is_generic(full_app_module, monkeypatch):
    app_module = full_app_module
    monkeypatch.setattr(
        app_module.db, "get_building", MagicMock(side_effect=RuntimeError("db down"))
    )

    client = app_module.web.test_client()
    resp = client.post(
        "/api/meter-data/upload",
        json={"building_id": "b-123", "csv_content": "x", "tier": 1},
    )

    assert resp.status_code == 500
    data = resp.get_json()
    assert "error" in data
    assert "db down" not in data["error"]


def test_meter_data_upload_save_consent_error_is_generic(full_app_module, monkeypatch):
    app_module = full_app_module
    monkeypatch.setattr(
        app_module.db, "get_building", lambda _bid: {"building_id": _bid}
    )
    monkeypatch.setattr(
        app_module.db,
        "save_data_consent",
        MagicMock(side_effect=RuntimeError("consent save failed")),
    )

    client = app_module.web.test_client()
    resp = client.post(
        "/api/meter-data/upload",
        json={"building_id": "b-123", "csv_content": "x", "tier": 1},
    )

    assert resp.status_code == 500
    data = resp.get_json()
    assert "error" in data
    assert "consent save failed" not in data["error"]


def test_meter_data_upload_does_not_expose_exception_text(full_app_module, monkeypatch):
    app_module = full_app_module
    import meter_data

    monkeypatch.setattr(
        meter_data, "ingest_file", MagicMock(side_effect=RuntimeError("disk full"))
    )
    monkeypatch.setattr(
        app_module.db, "get_building", lambda _bid: {"building_id": _bid}
    )

    client = app_module.web.test_client()
    resp = client.post(
        "/api/meter-data/upload",
        json={"building_id": "b-123", "csv_content": "x", "tier": 1},
    )

    assert resp.status_code == 500
    data = resp.get_json()
    assert "error" in data
    assert "disk full" not in data["error"]


def test_leg_cluster_does_not_expose_exception_text(client, monkeypatch):
    fake_ml = MagicMock()
    fake_ml.find_optimal_communities = MagicMock(
        side_effect=RuntimeError("cluster fail")
    )
    monkeypatch.setitem(sys.modules, "ml_models", fake_ml)

    resp = client.post(
        "/api/v1/leg/cluster",
        json={
            "buildings": [
                {"building_id": "a", "lat": 47.0, "lon": 8.0},
                {"building_id": "b", "lat": 47.0, "lon": 8.1},
            ]
        },
    )

    assert resp.status_code == 500
    data = resp.get_json()
    assert "error" in data
    assert "cluster fail" not in data["error"]


# ---------------------------------------------------------------------------
# Static / configuration fixes
# ---------------------------------------------------------------------------


def test_app_py_does_not_hardcode_debug_true():
    with open("app.py") as f:
        source = f.read()
    assert "app.run(debug=True" not in source


def test_lint_workflow_has_top_level_permissions():
    with open(".github/workflows/lint.yml") as f:
        content = f.read()
    assert "permissions:" in content


def test_test_workflow_has_top_level_permissions():
    with open(".github/workflows/deploy.yml") as f:
        content = f.read()
    assert "permissions:" in content
