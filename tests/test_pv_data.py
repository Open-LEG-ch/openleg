# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests für den PV-Nutzungsdaten-Loader."""

import pytest

import pv_data


def test_parse_snapshot_row_maps_fields():
    row = {
        "bfs_nr": "4021",
        "municipality_name": "Baden",
        "canton_code": "AG",
        "snapshot_year": "2026",
        "population_2019": "19900",
        "density_per_km2_2019": "1500.123456",
        "area_km2": "13.3",
        "annual_potential_gwh": "47.0",
        "estimated_potential_kw": "50000.0",
        "current_total_kw": "6250.0",
        "pv_utilization_score_pct_current": "12.5",
        "untapped_potential_kw_current": "43750.0",
    }
    rec = pv_data.parse_snapshot_row(row)
    assert rec["bfs_number"] == 4021
    assert rec["name"] == "Baden"
    assert rec["kanton"] == "AG"
    assert rec["population"] == 19900
    assert rec["density_per_km2"] == 1500.12
    assert rec["pv_score_pct"] == 12.5
    assert rec["pv_installed_kw"] == 6250.0
    assert rec["pv_snapshot_year"] == 2026
    assert rec["pv_plant_match_rate"] == pv_data.PLANT_MATCH_RATE_PCT


def test_parse_snapshot_row_skips_missing_bfs():
    assert pv_data.parse_snapshot_row({"bfs_nr": ""}) is None


def test_parse_panel_row_maps_fields():
    row = {
        "bfs_nr": "1",
        "year": "2017",
        "pv_added_initial_kw": "95.28",
        "pv_added_plants": "8",
        "pv_cumulative_initial_kw": "98.58",
        "estimated_potential_kw": "14572.7273",
        "pv_utilization_score_pct": "0.676469",
        "untapped_potential_kw": "14474.1473",
    }
    rec = pv_data.parse_panel_row(row)
    assert rec["bfs_number"] == 1
    assert rec["year"] == 2017
    assert rec["added_plants"] == 8
    assert rec["cumulative_kw"] == 98.58
    assert rec["score_pct"] == 0.6765


def test_seed_snapshot_csv_parses(tmp_path=None):
    """Erste Datenzeile der ausgelieferten Snapshot-CSV muss parsen."""
    first = next(pv_data.iter_csv(pv_data.SNAPSHOT_CSV))
    rec = pv_data.parse_snapshot_row(first)
    assert rec is not None
    assert rec["bfs_number"] > 0
    assert rec["pv_score_pct"] is not None


def test_snapshot_year_matches_csv():
    if not pv_data.SNAPSHOT_CSV.exists():
        pytest.skip("Snapshot-CSV fehlt")
    assert {row["snapshot_year"] for row in pv_data.iter_csv(pv_data.SNAPSHOT_CSV)} == {
        str(pv_data.SNAPSHOT_YEAR)
    }


def test_seed_panel_csv_parses():
    first = next(pv_data.iter_csv(pv_data.PANEL_CSV))
    rec = pv_data.parse_panel_row(first)
    assert rec is not None
    assert rec["year"] >= 2016


def test_load_snapshot_calls_upsert(monkeypatch):
    calls = []

    class _FakeDB:
        def upsert_municipality_pv(self, rec):
            calls.append(rec)
            return True

    monkeypatch.setitem(__import__("sys").modules, "database", _FakeDB())
    rows = [
        {"bfs_nr": "1", "municipality_name": "A", "canton_code": "ZH"},
        {"bfs_nr": "2", "municipality_name": "B", "canton_code": "ZH"},
    ]
    monkeypatch.setattr(pv_data, "iter_csv", lambda _p: iter(rows))
    n = pv_data.load_snapshot()
    assert n == 2
    assert [c["bfs_number"] for c in calls] == [1, 2]


def test_load_panel_batches(monkeypatch):
    saved = []

    class _FakeDB:
        def save_municipality_pv_panel(self, batch):
            saved.append(len(batch))
            return len(batch)

    monkeypatch.setitem(__import__("sys").modules, "database", _FakeDB())
    rows = [{"bfs_nr": str(i), "year": "2016"} for i in range(1, 6)]
    monkeypatch.setattr(pv_data, "iter_csv", lambda _p: iter(rows))
    total = pv_data.load_panel(batch_size=2)
    assert total == 5
    assert saved == [2, 2, 1]
