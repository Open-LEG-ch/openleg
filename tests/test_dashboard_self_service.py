# SPDX-License-Identifier: AGPL-3.0-or-later
"""TDD contracts for dashboard-owned profile and consent updates (#285)."""

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

import dashboard
import database
from store import dashboard_profile
from tests.test_dashboard_access_routes import _set_session
from tests.test_dashboard_access_routes import (  # noqa: F401
    app_module as dashboard_app_module,
)


def test_update_profile_validates_and_delegates(monkeypatch):
    save = MagicMock(return_value=True)
    monkeypatch.setattr(dashboard.db, "update_dashboard_profile", save)

    result = dashboard.update_profile(
        "building-1",
        annual_consumption_kwh="4200",
        potential_pv_kwp="8.5",
        share_with_utility=True,
        share_with_neighbors=False,
    )

    assert result == {"error": None}
    save.assert_called_once_with(
        "building-1",
        annual_consumption_kwh=4200.0,
        potential_pv_kwp=8.5,
        share_with_utility=True,
        share_with_neighbors=False,
    )


def test_update_profile_rejects_invalid_values_without_writing(monkeypatch):
    save = MagicMock()
    monkeypatch.setattr(dashboard.db, "update_dashboard_profile", save)

    result = dashboard.update_profile(
        "building-1",
        annual_consumption_kwh="-1",
        potential_pv_kwp="not-a-number",
        share_with_utility=False,
        share_with_neighbors=False,
    )

    assert result["error"] == "Bitte geben Sie einen gültigen Jahresverbrauch ein."
    save.assert_not_called()


class _Cursor:
    def __init__(self, one):
        self.one = one
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchone(self):
        return self.one

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def _connection(cursor):
    @contextmanager
    def factory():
        yield _Connection(cursor)

    return factory


def test_dashboard_profile_store_updates_profile_and_consents_atomically(monkeypatch):
    cursor = _Cursor({"building_id": "building-1"})
    monkeypatch.setattr(database, "get_connection", _connection(cursor))

    assert dashboard_profile.update_dashboard_profile(
        "building-1",
        annual_consumption_kwh=4200.0,
        potential_pv_kwp=8.5,
        share_with_utility=True,
        share_with_neighbors=False,
    )

    assert len(cursor.executed) == 2
    building_query, building_params = cursor.executed[0]
    consent_query, consent_params = cursor.executed[1]
    assert "UPDATE buildings" in building_query
    assert "RETURNING building_id" in building_query
    assert building_params == (4200.0, 8.5, "building-1")
    assert "INSERT INTO consents" in consent_query
    assert "ON CONFLICT (building_id) DO UPDATE" in " ".join(consent_query.split())
    assert consent_params == ("building-1", False, True)
    assert (
        database.update_dashboard_profile is dashboard_profile.update_dashboard_profile
    )


def test_dashboard_profile_store_does_not_create_orphan_consent(monkeypatch):
    cursor = _Cursor(None)
    monkeypatch.setattr(database, "get_connection", _connection(cursor))

    assert not dashboard_profile.update_dashboard_profile(
        "unknown",
        annual_consumption_kwh=4200.0,
        potential_pv_kwp=None,
        share_with_utility=False,
        share_with_neighbors=False,
    )
    assert len(cursor.executed) == 1


def test_profile_update_route_uses_session_identity_and_csrf(
    dashboard_app_module,  # noqa: F811
    monkeypatch,
):
    update = MagicMock(return_value={"error": None})
    monkeypatch.setattr(dashboard_app_module.dashboard_module, "update_profile", update)
    client = dashboard_app_module.web.test_client()
    _set_session(client)

    anonymous = dashboard_app_module.web.test_client().post(
        "/dashboard/profile",
        data={"csrf_token": "csrf-secret", "annual_consumption_kwh": "4200"},
    )
    assert anonymous.status_code == 401

    rejected = client.post(
        "/dashboard/profile",
        data={"csrf_token": "wrong", "annual_consumption_kwh": "4200"},
    )
    assert rejected.status_code == 400
    update.assert_not_called()

    accepted = client.post(
        "/dashboard/profile",
        data={
            "csrf_token": "csrf-secret",
            "bid": "building-attacker",
            "annual_consumption_kwh": "4200",
            "potential_pv_kwp": "8.5",
            "share_with_utility": "on",
        },
    )
    assert accepted.status_code == 302
    assert accepted.headers["Location"].endswith("/dashboard?saved=1")
    update.assert_called_once_with(
        "building-session",
        annual_consumption_kwh="4200",
        potential_pv_kwp="8.5",
        share_with_utility=True,
        share_with_neighbors=False,
    )


def test_dashboard_template_has_one_real_self_service_form():
    source = Path("templates/dashboard.html").read_text(encoding="utf-8")

    assert 'method="post" action="/dashboard/profile"' in source
    assert 'name="csrf_token" value="{{ csrf_token }}"' in source
    for field in (
        "annual_consumption_kwh",
        "potential_pv_kwp",
        "share_with_utility",
        "share_with_neighbors",
    ):
        assert f'name="{field}"' in source
    assert "EVU-Einwilligung%20erteilen" not in source
    assert "Nachbar-Einwilligung%20erteilen" not in source
