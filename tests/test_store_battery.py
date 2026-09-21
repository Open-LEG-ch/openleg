# SPDX-License-Identifier: AGPL-3.0-or-later
"""Behaviour tests for the Quartierakku asset store and its cost shares."""

from contextlib import contextmanager
from decimal import Decimal

import pytest

import database
from store import battery, billing


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


def _asset(**overrides):
    asset = {
        "name": "Quartierakku",
        "participant_id": "battery-a",
        "capacity_kwh": Decimal(45),
        "annual_cost_chf": Decimal(960),
        "shares": [
            ("building-a", Decimal(25)),
            ("building-b", Decimal(75)),
        ],
    }
    asset.update(overrides)
    return asset


def test_save_battery_asset_inserts_asset_and_shares(monkeypatch):
    cursor = _Cursor(one={"id": 9})
    monkeypatch.setattr(database, "get_connection", _conn(cursor))

    asset_id = battery.save_battery_asset("community-a", _asset())

    assert asset_id == 9
    assert len(cursor.executed) == 4
    insert, params = cursor.executed[1]
    assert "INSERT INTO billing_storage_assets" in insert
    assert params == (
        "community-a",
        "Quartierakku",
        Decimal(45),
        Decimal(960),
        "battery-a",
    )
    share_insert, share_params = cursor.executed[2]
    assert "INSERT INTO billing_storage_shares" in share_insert
    assert share_params == (9, "building-a", Decimal(25))


def test_save_battery_asset_replaces_the_previous_record(monkeypatch):
    cursor = _Cursor(one={"id": 9})
    monkeypatch.setattr(database, "get_connection", _conn(cursor))

    battery.save_battery_asset("community-a", _asset())

    delete, delete_params = cursor.executed[0]
    assert "DELETE FROM billing_storage_assets" in delete
    assert delete_params == ("community-a",)


def test_save_battery_asset_wraps_storage_failure(monkeypatch):
    cursor = _Cursor(error=RuntimeError("database unavailable"))
    monkeypatch.setattr(database, "get_connection", _conn(cursor))

    with pytest.raises(billing.BillingStoreError):
        battery.save_battery_asset("community-a", _asset())


def test_get_battery_asset_returns_none_without_a_record(monkeypatch):
    cursor = _Cursor(one=None)
    monkeypatch.setattr(database, "get_connection", _conn(cursor))

    assert battery.get_battery_asset("community-a") is None


def test_get_battery_asset_returns_shares_as_decimals(monkeypatch):
    asset_row = {
        "id": 9,
        "community_id": "community-a",
        "name": "Quartierakku",
        "capacity_kwh": Decimal(45),
        "annual_cost_chf": Decimal(960),
    }
    share_rows = [
        {"participant_id": "building-a", "share_pct": Decimal("25.0")},
        {"participant_id": "building-b", "share_pct": Decimal("75.0")},
    ]
    cursor = _Cursor(one=asset_row, rows=share_rows)
    monkeypatch.setattr(database, "get_connection", _conn(cursor))

    asset = battery.get_battery_asset("community-a")

    assert asset["name"] == "Quartierakku"
    assert asset["shares"] == [
        ("building-a", Decimal("25.0")),
        ("building-b", Decimal("75.0")),
    ]


def test_get_battery_asset_wraps_storage_failure(monkeypatch):
    cursor = _Cursor(error=RuntimeError("database unavailable"))
    monkeypatch.setattr(database, "get_connection", _conn(cursor))

    with pytest.raises(billing.BillingStoreError):
        battery.get_battery_asset("community-a")


def test_update_battery_asset_rewrites_in_place(monkeypatch):
    cursor = _Cursor(one={"id": 11})
    monkeypatch.setattr(database, "get_connection", _conn(cursor))

    asset_id = battery.update_battery_asset("community-a", _asset())

    assert asset_id == 11
    assert "DELETE FROM billing_storage_assets" in cursor.executed[0][0]


def test_database_reexports_the_battery_seams():
    for name in ("save_battery_asset", "get_battery_asset", "update_battery_asset"):
        assert getattr(database, name) is getattr(battery, name), name
