# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for PV-Nutzungs-Schema (Snapshot-Spalten + Panel)."""

from contextlib import contextmanager

import psycopg2.extras

import database


class _FakeCursor:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def _conn_ctx(cursor):
    @contextmanager
    def _factory():
        yield _FakeConnection(cursor)

    return _factory


def test_upsert_municipality_pv_writes_pv_columns(monkeypatch):
    cur = _FakeCursor()
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))
    ok = database.upsert_municipality_pv(
        {
            "bfs_number": 4021,
            "name": "Baden",
            "kanton": "AG",
            "population": 19900,
            "density_per_km2": 1500.0,
            "area_km2": 13.3,
            "pv_score_pct": 12.5,
            "pv_estimated_potential_kw": 50000.0,
            "pv_installed_kw": 6250.0,
            "pv_untapped_kw": 43750.0,
            "pv_annual_potential_gwh": 47.0,
            "pv_snapshot_year": 2026,
            "pv_plant_match_rate": 76.89,
        }
    )
    assert ok is True
    query, params = cur.executed[0]
    assert "pv_score_pct" in query
    assert "ON CONFLICT (bfs_number)" in query
    assert 4021 in params
    assert "Baden" in params


def test_upsert_municipality_pv_preserves_other_fields_in_sql(monkeypatch):
    cur = _FakeCursor()
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))
    database.upsert_municipality_pv({"bfs_number": 1, "name": "Test"})
    query = cur.executed[0][0]
    # ElCom-/Solar-Felder dürfen nicht angefasst werden
    assert "solar_potential_pct" not in query
    assert "energy_transition_score" not in query


def test_get_municipality_pv_panel_orders_and_dicts(monkeypatch):
    cur = _FakeCursor(
        rows=[
            {"bfs_number": 1, "year": 2016, "score_pct": 0.02},
            {"bfs_number": 1, "year": 2017, "score_pct": 0.68},
        ]
    )
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))
    rows = database.get_municipality_pv_panel(1)
    assert [r["year"] for r in rows] == [2016, 2017]
    assert "ORDER BY year" in cur.executed[0][0]


def test_save_municipality_pv_panel_bulk(monkeypatch):
    cur = _FakeCursor()
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))
    captured = {}

    def fake_execute_values(_cur, query, values, *_a, **_k):
        captured["query"] = query
        captured["values"] = values

    monkeypatch.setattr(psycopg2.extras, "execute_values", fake_execute_values)
    n = database.save_municipality_pv_panel(
        [
            {
                "bfs_number": 1,
                "year": 2016,
                "added_kw": 3.3,
                "added_plants": 2,
                "cumulative_kw": 3.3,
                "estimated_potential_kw": 14572.7,
                "score_pct": 0.02,
                "untapped_kw": 14569.4,
            }
        ]
    )
    assert n == 1
    assert "municipality_pv_panel" in captured["query"]
    assert "ON CONFLICT (bfs_number, year)" in captured["query"]
    assert captured["values"][0][0] == 1


def test_save_municipality_pv_panel_empty_returns_zero():
    assert database.save_municipality_pv_panel([]) == 0
