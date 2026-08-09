# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for canton-scoped refresh behavior."""

import database
import public_data


def test_refresh_canton_non_zh_does_not_union_zh_seed(monkeypatch):
    saved_profiles = []

    monkeypatch.setattr(
        public_data,
        "fetch_energie_reporter",
        lambda: [
            {"bfs_number": 1001, "name": "AG Town", "kanton": "AG"},
            {"bfs_number": 261, "name": "Dietikon", "kanton": "ZH"},
        ],
    )
    monkeypatch.setattr(public_data, "fetch_sonnendach_municipal", list)
    monkeypatch.setattr(public_data, "fetch_elcom_tariffs", lambda _bfs, year=2026: [])

    monkeypatch.setattr(database, "save_sonnendach_municipal", lambda _entry: True)
    monkeypatch.setattr(database, "save_elcom_tariffs", lambda _rows: 0)
    monkeypatch.setattr(
        database,
        "save_municipality_profile",
        lambda profile: saved_profiles.append(profile) or True,
    )

    result = public_data.refresh_canton("AG", year=2026)
    assert result["kanton"] == "AG"
    assert result["municipalities"] == 1
    assert [p["bfs_number"] for p in saved_profiles] == [1001]


def test_refresh_canton_zh_keeps_seed_list(monkeypatch):
    saved_profiles = []

    monkeypatch.setattr(public_data, "ZH_BFS_NUMBERS", [261, 247])
    monkeypatch.setattr(public_data, "fetch_energie_reporter", list)
    monkeypatch.setattr(public_data, "fetch_sonnendach_municipal", list)
    monkeypatch.setattr(public_data, "fetch_elcom_tariffs", lambda _bfs, year=2026: [])

    monkeypatch.setattr(database, "save_sonnendach_municipal", lambda _entry: True)
    monkeypatch.setattr(database, "save_elcom_tariffs", lambda _rows: 0)
    monkeypatch.setattr(
        database,
        "save_municipality_profile",
        lambda profile: saved_profiles.append(profile) or True,
    )

    result = public_data.refresh_canton("ZH", year=2026)
    assert result["kanton"] == "ZH"
    assert {p["bfs_number"] for p in saved_profiles} == {247, 261}


def test_refresh_canton_does_not_leak_exception_detail(monkeypatch):
    """Per-municipality failures must not expose exception text in the result.

    Guards against py/stack-trace-exposure: refresh_canton is returned via
    jsonify from a cron endpoint, so raw str(e) would flow to the response.
    """
    secret = "SECRET_DB_HOST=10.0.0.9 password=hunter2"

    monkeypatch.setattr(public_data, "ZH_BFS_NUMBERS", [261])
    monkeypatch.setattr(public_data, "fetch_energie_reporter", list)
    monkeypatch.setattr(public_data, "fetch_sonnendach_municipal", list)

    def _boom(_bfs, year=2026):
        raise RuntimeError(secret)

    monkeypatch.setattr(public_data, "fetch_elcom_tariffs", _boom)
    monkeypatch.setattr(database, "save_sonnendach_municipal", lambda _entry: True)
    monkeypatch.setattr(database, "save_elcom_tariffs", lambda _rows: 0)
    monkeypatch.setattr(database, "save_municipality_profile", lambda profile: True)

    result = public_data.refresh_canton("ZH", year=2026)

    assert result["errors"], "expected the failing municipality to be recorded"
    for entry in result["errors"]:
        assert entry.get("bfs") == 261
        assert secret not in str(entry)
        assert "hunter2" not in str(entry)
