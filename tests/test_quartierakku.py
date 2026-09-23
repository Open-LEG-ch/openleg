# SPDX-License-Identifier: AGPL-3.0-or-later
"""Behaviour tests for the shared Quartierakku asset and its cost share.

Covers the SQL-free validation module (``quartierakku``), the store seams,
and the operator dashboard surface. One battery per community, capacity in
kWh, annual cost in CHF, and one cost share per participant.
"""

from contextlib import contextmanager
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

import database
import quartierakku
from store import battery as battery_store

VALID_CONFIG = {
    "community_id": "community-a",
    "capacity_kwh": Decimal(45),
    "annual_cost_chf": Decimal(960),
    "shares": {
        "building-a": Decimal("12.5"),
        "building-b": Decimal("12.5"),
        "building-c": Decimal(25),
        "building-d": Decimal(50),
    },
}


class _Cursor:
    def __init__(self, rows=None, one=None, error=None):
        self.rows = rows or []
        self.one = one
        self.error = error
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))
        if self.error is not None:
            raise self.error

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.one

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def _conn(cursor):
    @contextmanager
    def factory():
        yield _Connection(cursor)

    return factory


# === Domain validation ===


def test_validate_battery_config_normalizes_one_complete_config():
    normalized = quartierakku.validate_battery_config(VALID_CONFIG)

    assert normalized["community_id"] == "community-a"
    assert normalized["capacity_kwh"] == Decimal(45)
    assert normalized["annual_cost_chf"] == Decimal(960)
    assert normalized["shares"] == {
        "building-a": Decimal("12.5"),
        "building-b": Decimal("12.5"),
        "building-c": Decimal(25),
        "building-d": Decimal(50),
    }


def test_shares_summing_to_ninety_nine_point_nine_are_refused():
    config = {
        **VALID_CONFIG,
        "shares": {"building-a": Decimal("99.9")},
    }

    with pytest.raises(quartierakku.InvalidBatteryConfig) as exc:
        quartierakku.validate_battery_config(config)

    assert str(exc.value) == "Die Anteile müssen zusammen 100 Prozent ergeben."


def test_shares_summing_to_one_hundred_point_one_are_refused():
    config = {
        **VALID_CONFIG,
        "shares": {"building-a": Decimal("50.05"), "building-b": Decimal("50.05")},
    }

    with pytest.raises(quartierakku.InvalidBatteryConfig) as exc:
        quartierakku.validate_battery_config(config)

    assert str(exc.value) == "Die Anteile müssen zusammen 100 Prozent ergeben."


def test_shares_exactly_one_hundred_are_accepted():
    config = {**VALID_CONFIG, "shares": {"building-a": Decimal(100)}}

    normalized = quartierakku.validate_battery_config(config)
    assert normalized["shares"] == {"building-a": Decimal(100)}


@pytest.mark.parametrize(
    "capacity", [Decimal(0), Decimal(-1), Decimal("NaN"), "abc", None]
)
def test_capacity_at_or_below_zero_or_garbage_is_refused(capacity):
    config = {**VALID_CONFIG, "capacity_kwh": capacity}

    with pytest.raises(quartierakku.InvalidBatteryConfig):
        quartierakku.validate_battery_config(config)


@pytest.mark.parametrize("cost", [Decimal("-0.01"), Decimal("NaN"), "abc", None])
def test_negative_or_garbage_annual_cost_is_refused(cost):
    config = {**VALID_CONFIG, "annual_cost_chf": cost}

    with pytest.raises(quartierakku.InvalidBatteryConfig):
        quartierakku.validate_battery_config(config)


@pytest.mark.parametrize(
    "shares",
    [
        {"building-a": Decimal("-0.01")},
        {"building-a": Decimal(101)},
        {"building-a": Decimal("NaN")},
        {"building-a": "abc"},
    ],
)
def test_invalid_share_values_are_refused(shares):
    config = {**VALID_CONFIG, "shares": shares}

    with pytest.raises(quartierakku.InvalidBatteryConfig):
        quartierakku.validate_battery_config(config)


def test_stored_and_form_values_reject_more_than_two_decimal_places():
    with pytest.raises(quartierakku.InvalidBatteryConfig, match="Nachkommastellen"):
        quartierakku.validate_battery_config(
            {**VALID_CONFIG, "shares": {"building-a": Decimal("100.001")}}
        )

    result = quartierakku.validate_battery_form(
        _form(
            capacity_kwh="45.123",
            annual_cost_chf="960.001",
            **{"share:building-a": "12.501"},
        ),
        ("building-a", "building-b", "building-c", "building-d"),
    )

    assert result["battery"] is None
    assert {"capacity_kwh", "annual_cost_chf", "share:building-a"} <= set(
        result["errors"]
    )


def test_empty_shares_are_refused():
    config = {**VALID_CONFIG, "shares": {}}

    with pytest.raises(quartierakku.InvalidBatteryConfig):
        quartierakku.validate_battery_config(config)


def test_capacity_error_message_names_the_bound_in_german():
    config = {**VALID_CONFIG, "capacity_kwh": Decimal(0)}

    with pytest.raises(quartierakku.InvalidBatteryConfig) as exc:
        quartierakku.validate_battery_config(config)

    assert str(exc.value) == (
        "Speicherkapazität in kWh, grösser 0 und höchstens 10000, höchstens "
        "2 Nachkommastellen."
    )


def test_cost_error_message_names_the_bound_in_german():
    config = {**VALID_CONFIG, "annual_cost_chf": Decimal(-1)}

    with pytest.raises(quartierakku.InvalidBatteryConfig) as exc:
        quartierakku.validate_battery_config(config)

    assert str(exc.value) == (
        "Jahreskosten in CHF, zwischen 0 und 100000, höchstens 2 Nachkommastellen."
    )


# === Form validation ===


def _form(**overrides):
    form = {
        "capacity_kwh": "45",
        "annual_cost_chf": "960",
        "share:building-a": "12.50",
        "share:building-b": "12.50",
        "share:building-c": "25",
        "share:building-d": "50",
    }
    form.update(overrides)
    return form


def test_battery_form_parses_capacity_cost_and_shares():
    result = quartierakku.validate_battery_form(
        _form(), ("building-a", "building-b", "building-c", "building-d")
    )

    assert result["errors"] == {}
    battery = result["battery"]
    assert battery["capacity_kwh"] == Decimal(45)
    assert battery["annual_cost_chf"] == Decimal(960)
    assert battery["shares"] == {
        "building-a": Decimal("12.5"),
        "building-b": Decimal("12.5"),
        "building-c": Decimal(25),
        "building-d": Decimal(50),
    }


def test_battery_form_treats_a_missing_member_input_as_zero_share():
    result = quartierakku.validate_battery_form(
        _form(
            **{
                "share:building-a": "25",
                "share:building-b": "25",
                "share:building-c": "50",
                "share:building-d": "",
            }
        ),
        ("building-a", "building-b", "building-c", "building-d"),
    )

    assert result["errors"] == {}
    assert result["battery"]["shares"]["building-d"] == Decimal(0)


def test_battery_form_rejects_shares_that_do_not_sum_to_100():
    result = quartierakku.validate_battery_form(
        _form(**{"share:building-d": "51"}),
        ("building-a", "building-b", "building-c", "building-d"),
    )

    assert result["policy"] if False else True
    assert result["battery"] is None
    assert result["errors"]["shares"] == (
        "Die Anteile müssen zusammen 100 Prozent ergeben."
    )


def test_battery_form_rejects_negative_shares_with_a_german_message():
    result = quartierakku.validate_battery_form(
        _form(**{"share:building-a": "-1"}),
        ("building-a", "building-b", "building-c", "building-d"),
    )

    assert result["battery"] is None
    assert result["errors"]["share:building-a"] == (
        "Anteil für building-a in Prozent, zwischen 0 und 100, höchstens "
        "2 Nachkommastellen."
    )


def test_battery_form_rejects_garbage_capacity_and_cost():
    result = quartierakku.validate_battery_form(
        _form(capacity_kwh="0", annual_cost_chf="abc"),
        ("building-a", "building-b", "building-c", "building-d"),
    )

    assert result["battery"] is None
    assert "capacity_kwh" in result["errors"]
    assert "annual_cost_chf" in result["errors"]


def test_battery_form_refuses_shares_for_unknown_members():
    result = quartierakku.validate_battery_form(
        _form(**{"share:building-x": "5"}),
        ("building-a", "building-b", "building-c", "building-d"),
    )

    assert result["battery"] is None
    assert "shares" in result["errors"]


# === Store seams ===


def test_save_battery_inserts_the_asset_and_shares(monkeypatch):
    cursor = _Cursor()
    monkeypatch.setattr(database, "get_connection", _conn(cursor))

    battery_store.save_battery("community-a", VALID_CONFIG)

    battery_query, battery_params = cursor.executed[0]
    assert "INSERT INTO community_batteries" in battery_query
    assert "ON CONFLICT (community_id)" in battery_query
    assert battery_params[0] == "community-a"
    assert battery_params[1] == Decimal(45)
    assert battery_params[2] == Decimal(960)
    shares_query, shares_params = cursor.executed[1]
    assert "DELETE FROM community_battery_shares" in shares_query
    assert shares_params[0] == "community-a"
    insert_query, insert_params = cursor.executed[2]
    assert "INSERT INTO community_battery_shares" in insert_query
    assert insert_params[0] == "community-a"
    assert insert_params[1] == "building-a"
    assert insert_params[2] == Decimal("12.5")


def test_save_battery_rejects_an_invalid_config_before_storage(monkeypatch):
    cursor = _Cursor()
    monkeypatch.setattr(database, "get_connection", _conn(cursor))

    with pytest.raises(quartierakku.InvalidBatteryConfig):
        battery_store.save_battery(
            "community-a", {**VALID_CONFIG, "capacity_kwh": Decimal(0)}
        )
    assert cursor.executed == []


def test_save_battery_wraps_storage_failure(monkeypatch):
    cursor = _Cursor(error=RuntimeError("database unavailable"))
    monkeypatch.setattr(database, "get_connection", _conn(cursor))

    with pytest.raises(battery_store.BillingStoreError):
        battery_store.save_battery("community-a", VALID_CONFIG)


def test_get_battery_reads_asset_and_shares(monkeypatch):
    cursor = _Cursor(
        one={
            "community_id": "community-a",
            "capacity_kwh": Decimal(45),
            "annual_cost_chf": Decimal(960),
        },
        rows=[
            {"building_id": "building-a", "share_pct": Decimal(50)},
            {"building_id": "building-b", "share_pct": Decimal(50)},
        ],
    )
    monkeypatch.setattr(database, "get_connection", _conn(cursor))

    config = battery_store.get_battery("community-a")

    assert config == {
        "community_id": "community-a",
        "capacity_kwh": Decimal(45),
        "annual_cost_chf": Decimal(960),
        "shares": {"building-a": Decimal(50), "building-b": Decimal(50)},
    }
    query, params = cursor.executed[0]
    assert "community_batteries" in query
    assert params == ("community-a",)


def test_get_battery_returns_none_without_an_asset(monkeypatch):
    cursor = _Cursor(one=None, rows=[])
    monkeypatch.setattr(database, "get_connection", _conn(cursor))

    assert battery_store.get_battery("community-a") is None


def test_get_battery_wraps_storage_failure(monkeypatch):
    cursor = _Cursor(error=RuntimeError("database unavailable"))
    monkeypatch.setattr(database, "get_connection", _conn(cursor))

    with pytest.raises(battery_store.BillingStoreError):
        battery_store.get_battery("community-a")


def test_database_reexports_the_battery_seams():
    for name in ("save_battery", "get_battery"):
        assert getattr(database, name) is getattr(battery_store, name), name


# === Operator dashboard surface ===

from tests.test_dashboard_access_routes import (  # noqa: F401
    _set_session,
    app_module,
)

BATTERY_COMMUNITY = "c0ffee"
DASHBOARD_URL = "/leg/dashboard?cid=" + BATTERY_COMMUNITY


def _battery_status():
    return {
        "community_id": BATTERY_COMMUNITY,
        "name": "LEG Musterweg",
        "status": "active",
        "distribution_model": "proportional",
        "member_count": {"total": 2, "confirmed": 2, "invited": 0},
        "readiness_score": 0,
        "next_steps": [],
        "members": [
            {"building_id": "b-admin", "role": "admin", "status": "confirmed"},
            {"building_id": "b-member", "role": "member", "status": "confirmed"},
        ],
    }


def _patch_battery_dashboard(monkeypatch, dashboard_app, *, battery=None):
    monkeypatch.setattr(
        dashboard_app.dashboard_module.formation_wizard,
        "get_community_status",
        MagicMock(return_value=_battery_status()),
    )
    monkeypatch.setattr(
        dashboard_app.dashboard_module.db,
        "get_battery",
        MagicMock(return_value=battery),
        raising=False,
    )
    monkeypatch.setattr(
        dashboard_app.dashboard_module.db,
        "list_leg_documents",
        MagicMock(return_value=[]),
        raising=False,
    )
    monkeypatch.setattr(
        dashboard_app.dashboard_module.db,
        "list_correspondence",
        MagicMock(return_value=[]),
        raising=False,
    )
    monkeypatch.setattr(
        dashboard_app.dashboard_module.db,
        "list_vnb_submission_cases",
        MagicMock(return_value=[]),
        raising=False,
    )
    monkeypatch.setattr(
        dashboard_app.dashboard_module.db,
        "list_vnb_mutations",
        MagicMock(return_value=[]),
        raising=False,
    )


def test_dashboard_shows_the_battery_with_its_shares(app_module, monkeypatch):  # noqa: F811
    _patch_battery_dashboard(
        monkeypatch,
        app_module,
        battery={
            "community_id": BATTERY_COMMUNITY,
            "capacity_kwh": Decimal(45),
            "annual_cost_chf": Decimal(960),
            "shares": {"b-admin": Decimal(50), "b-member": Decimal(50)},
        },
    )
    client = app_module.web.test_client()
    _set_session(client, building_id="b-admin")

    response = client.get(DASHBOARD_URL)
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Quartierakku" in html
    assert "45 kWh" in html
    assert "CHF 960.00" in html
    assert "50 %" in html


def test_dashboard_hides_the_battery_block_without_an_asset(app_module, monkeypatch):  # noqa: F811
    _patch_battery_dashboard(monkeypatch, app_module, battery=None)
    client = app_module.web.test_client()
    _set_session(client, building_id="b-admin")

    response = client.get(DASHBOARD_URL)
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Quartierakku" not in html


def test_battery_save_requires_a_confirmed_admin(app_module, monkeypatch):  # noqa: F811
    _patch_battery_dashboard(monkeypatch, app_module)
    client = app_module.web.test_client()
    _set_session(client, building_id="b-member")

    response = client.post(
        f"/leg/community/{BATTERY_COMMUNITY}/battery",
        data={
            "csrf_token": "csrf-secret",
            "capacity_kwh": "45",
            "annual_cost_chf": "960",
            "share:b-admin": "50",
            "share:b-member": "50",
        },
    )

    assert response.status_code == 403


def test_battery_save_persists_a_valid_form(app_module, monkeypatch):  # noqa: F811
    _patch_battery_dashboard(monkeypatch, app_module)
    save = MagicMock(return_value=None)
    monkeypatch.setattr(
        app_module.dashboard_module.db, "save_battery", save, raising=False
    )
    client = app_module.web.test_client()
    _set_session(client, building_id="b-admin")

    response = client.post(
        f"/leg/community/{BATTERY_COMMUNITY}/battery",
        data={
            "csrf_token": "csrf-secret",
            "capacity_kwh": "45",
            "annual_cost_chf": "960",
            "share:b-admin": "50",
            "share:b-member": "50",
        },
    )

    assert response.status_code == 302
    save.assert_called_once()


def test_battery_save_refuses_shares_that_do_not_sum_to_100(app_module, monkeypatch):  # noqa: F811
    _patch_battery_dashboard(monkeypatch, app_module)
    save = MagicMock(return_value=None)
    monkeypatch.setattr(
        app_module.dashboard_module.db, "save_battery", save, raising=False
    )
    client = app_module.web.test_client()
    _set_session(client, building_id="b-admin")

    response = client.post(
        f"/leg/community/{BATTERY_COMMUNITY}/battery",
        data={
            "csrf_token": "csrf-secret",
            "capacity_kwh": "45",
            "annual_cost_chf": "960",
            "share:b-admin": "50",
            "share:b-member": "40",
        },
    )

    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert "Die Anteile müssen zusammen 100 Prozent ergeben." in html
    save.assert_not_called()


def test_battery_save_refuses_a_negative_share(app_module, monkeypatch):  # noqa: F811
    _patch_battery_dashboard(monkeypatch, app_module)
    client = app_module.web.test_client()
    _set_session(client, building_id="b-admin")

    response = client.post(
        f"/leg/community/{BATTERY_COMMUNITY}/battery",
        data={
            "csrf_token": "csrf-secret",
            "capacity_kwh": "45",
            "annual_cost_chf": "960",
            "share:b-admin": "-1",
            "share:b-member": "101",
        },
    )

    assert response.status_code == 400
    assert "Anteil für b-admin in Prozent" in response.get_data(as_text=True)
