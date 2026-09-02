# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for canton-scoped refresh behavior."""

import logging
from datetime import datetime, timedelta

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
    monkeypatch.setattr(database, "get_municipality_profile", lambda _bfs: None)
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
    monkeypatch.setattr(database, "get_municipality_profile", lambda _bfs: None)
    monkeypatch.setattr(
        database,
        "save_municipality_profile",
        lambda profile: saved_profiles.append(profile) or True,
    )

    result = public_data.refresh_canton("ZH", year=2026)
    assert result["kanton"] == "ZH"
    assert result["municipalities"] == 2
    assert {p["bfs_number"] for p in saved_profiles} == {247, 261}


def test_refresh_canton_delegates_selected_bfs_once_with_single_bulk_fetch(
    monkeypatch,
):
    """refresh_canton must delegate the selected BFS through refresh_municipality
    exactly once, preloading the bulk results so Energie Reporter and Sonnendach
    are each fetched exactly once for the whole canton run.
    """
    saved_profiles, _saved_sonnendach = _patch_profile_repository(monkeypatch)
    calls = {"municipality": [], "energie_reporter": 0, "sonnendach": 0}
    reporter_row = {"bfs_number": 261, "name": "Reporter Gemeinde", "kanton": "ZH"}
    solar_row = {"bfs_number": 261, "potential_kwp": 310.0}

    real_refresh_municipality = public_data.refresh_municipality

    def _spy_refresh_municipality(bfs_number, year=2026, **kwargs):
        calls["municipality"].append(bfs_number)
        return real_refresh_municipality(bfs_number, year=year, **kwargs)

    def _spy_energie_reporter():
        calls["energie_reporter"] += 1
        return [reporter_row]

    def _spy_sonnendach():
        calls["sonnendach"] += 1
        return [solar_row]

    monkeypatch.setattr(public_data, "refresh_municipality", _spy_refresh_municipality)
    monkeypatch.setattr(public_data, "fetch_energie_reporter", _spy_energie_reporter)
    monkeypatch.setattr(public_data, "fetch_sonnendach_municipal", _spy_sonnendach)

    result = public_data.refresh_canton("ZH", year=2026)

    assert calls["municipality"] == [261]
    assert calls["energie_reporter"] == 1
    assert calls["sonnendach"] == 1
    assert result["municipalities"] == 1
    assert [p["bfs_number"] for p in saved_profiles] == [261]
    assert saved_profiles[0]["name"] == "Reporter Gemeinde"
    assert saved_profiles[0]["solar_installed_kwp"] == 310.0


def test_refresh_canton_continues_past_skipped_bfs_in_sorted_order(monkeypatch):
    """A skipped municipality must not stop later seed BFS values.

    Mutation guard for refresh_canton's persistence loop: a municipality
    reporting persistence == "skipped" must not end the loop, and the
    selected seed BFS values must be visited in deterministic ascending
    order instead of set-iteration order.
    """
    calls = []
    outcomes = {
        247: {"bfs_number": 247, "sources": {}, "persistence": "skipped"},
        261: {"bfs_number": 261, "sources": {}},
    }

    def _spy_refresh_municipality(bfs_number, year=2026, **kwargs):
        calls.append(bfs_number)
        return outcomes[bfs_number]

    monkeypatch.setattr(public_data, "ZH_BFS_NUMBERS", [261, 247])
    monkeypatch.setattr(public_data, "fetch_energie_reporter", list)
    monkeypatch.setattr(public_data, "fetch_sonnendach_municipal", list)
    monkeypatch.setattr(public_data, "refresh_municipality", _spy_refresh_municipality)

    result = public_data.refresh_canton("ZH", year=2026)

    assert calls == [247, 261]
    assert result["municipalities"] == 1


def test_refresh_canton_does_not_leak_exception_detail(monkeypatch, caplog):
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

    with caplog.at_level(logging.ERROR):
        result = public_data.refresh_canton("ZH", year=2026)

    assert result["errors"], "expected the failing municipality to be recorded"
    for entry in result["errors"]:
        assert entry.get("bfs") == 261
        assert secret not in str(entry)
        assert "hunter2" not in str(entry)
    assert secret not in caplog.text
    assert "hunter2" not in caplog.text


_EXISTING_PROFILE = {
    "bfs_number": 261,
    "name": "Bestehende Gemeinde",
    "kanton": "ZH",
    "population": 12345,
    "solar_potential_pct": 41.0,
    "solar_installed_kwp": 222.0,
    "ev_share_pct": 12.0,
    "renewable_heating_pct": 55.0,
    "electricity_consumption_mwh": 1000.0,
    "renewable_production_mwh": 350.0,
    "leg_value_gap_chf": 90.0,
    "energy_transition_score": 44.0,
    "data_sources": {"existing": True},
}


def _patch_profile_repository(monkeypatch):
    saved_profiles = []
    saved_sonnendach = []
    monkeypatch.setattr(public_data, "ZH_BFS_NUMBERS", [261])
    monkeypatch.setattr(public_data, "fetch_elcom_tariffs", lambda _bfs, year=2026: [])
    monkeypatch.setattr(public_data, "fetch_sonnendach_municipal", list)
    monkeypatch.setattr(database, "save_elcom_tariffs", lambda _rows: 0)
    monkeypatch.setattr(
        database,
        "get_municipality_profile",
        lambda _bfs: dict(_EXISTING_PROFILE),
    )
    monkeypatch.setattr(
        database,
        "save_municipality_profile",
        lambda profile: saved_profiles.append(profile) or True,
    )
    monkeypatch.setattr(
        database,
        "save_sonnendach_municipal",
        lambda entry: saved_sonnendach.append(entry) or True,
    )
    return saved_profiles, saved_sonnendach


def test_energie_reporter_outage_preserves_its_fields_and_saves_sonnendach(
    monkeypatch,
):
    saved_profiles, saved_sonnendach = _patch_profile_repository(monkeypatch)
    solar = {"bfs_number": 261, "potential_kwp": 999.0}
    monkeypatch.setattr(public_data, "fetch_energie_reporter", lambda: None)
    monkeypatch.setattr(public_data, "fetch_sonnendach_municipal", lambda: [solar])

    result = public_data.refresh_canton("ZH", year=2026)

    assert result["errors"] == [{"source": "energie_reporter", "error": "fetch_failed"}]
    assert saved_sonnendach == [solar]
    assert len(saved_profiles) == 1
    profile = saved_profiles[0]
    for field in (
        "name",
        "kanton",
        "population",
        "solar_potential_pct",
        "ev_share_pct",
        "renewable_heating_pct",
        "electricity_consumption_mwh",
        "renewable_production_mwh",
        "energy_transition_score",
    ):
        assert profile[field] == _EXISTING_PROFILE[field]
    assert profile["solar_installed_kwp"] == 999.0
    assert profile["data_sources"]["existing"] is True
    assert "energie_reporter" not in profile["data_sources"]


def test_sonnendach_outage_preserves_solar_fields_and_saves_energie_reporter(
    monkeypatch,
):
    saved_profiles, saved_sonnendach = _patch_profile_repository(monkeypatch)
    reporter = {
        "bfs_number": 261,
        "name": "Neue Gemeinde",
        "kanton": "ZH",
        "population": 13000,
        "solar_potential_pct": 60.0,
        "ev_share_pct": 20.0,
        "renewable_heating_pct": 70.0,
        "electricity_consumption_mwh": 1200.0,
        "renewable_production_mwh": 600.0,
    }
    monkeypatch.setattr(public_data, "fetch_energie_reporter", lambda: [reporter])
    monkeypatch.setattr(public_data, "fetch_sonnendach_municipal", lambda: None)

    result = public_data.refresh_canton("ZH", year=2026)

    assert result["errors"] == [{"source": "sonnendach", "error": "fetch_failed"}]
    assert saved_sonnendach == []
    assert len(saved_profiles) == 1
    profile = saved_profiles[0]
    assert profile["name"] == "Neue Gemeinde"
    assert profile["population"] == 13000
    assert (
        profile["energy_transition_score"]
        != _EXISTING_PROFILE["energy_transition_score"]
    )
    assert profile["solar_installed_kwp"] == _EXISTING_PROFILE["solar_installed_kwp"]
    assert profile["data_sources"]["existing"] is True
    assert "sonnendach" not in profile["data_sources"]


def test_healthy_partial_sources_preserve_a_missing_seed_profile(monkeypatch):
    saved_profiles, saved_sonnendach = _patch_profile_repository(monkeypatch)
    monkeypatch.setattr(public_data, "fetch_energie_reporter", list)
    monkeypatch.setattr(public_data, "fetch_sonnendach_municipal", list)

    result = public_data.refresh_canton("ZH", year=2026)

    assert result["errors"] == []
    assert saved_sonnendach == []
    assert len(saved_profiles) == 1
    profile = saved_profiles[0]
    for field in (
        "name",
        "kanton",
        "population",
        "solar_potential_pct",
        "solar_installed_kwp",
        "ev_share_pct",
        "renewable_heating_pct",
        "electricity_consumption_mwh",
        "renewable_production_mwh",
        "energy_transition_score",
    ):
        assert profile[field] == _EXISTING_PROFILE[field]


def test_full_bulk_source_outage_does_not_save_seeded_profiles(monkeypatch):
    saved_profiles, saved_sonnendach = _patch_profile_repository(monkeypatch)
    monkeypatch.setattr(public_data, "fetch_energie_reporter", lambda: None)
    monkeypatch.setattr(public_data, "fetch_sonnendach_municipal", lambda: None)

    result = public_data.refresh_canton("ZH", year=2026)

    assert result["municipalities"] == 0
    assert result["errors"] == [
        {"source": "energie_reporter", "error": "fetch_failed"},
        {"source": "sonnendach", "error": "fetch_failed"},
    ]
    assert saved_profiles == []
    assert saved_sonnendach == []


def test_bulk_source_outage_still_saves_an_elcom_update(monkeypatch):
    saved_profiles, _saved_sonnendach = _patch_profile_repository(monkeypatch)
    tariff = {
        "bfs_number": 261,
        "category": "H4",
        "grid_rp_kwh": 10,
        "total_rp_kwh": 20,
    }
    monkeypatch.setattr(public_data, "fetch_energie_reporter", lambda: None)
    monkeypatch.setattr(public_data, "fetch_sonnendach_municipal", lambda: None)
    monkeypatch.setattr(
        public_data, "fetch_elcom_tariffs", lambda _bfs, year=2026: [tariff]
    )

    result = public_data.refresh_canton("ZH", year=2026)

    assert result["municipalities"] == 1
    assert len(saved_profiles) == 1
    profile = saved_profiles[0]
    assert profile["name"] == _EXISTING_PROFILE["name"]
    assert profile["solar_installed_kwp"] == _EXISTING_PROFILE["solar_installed_kwp"]
    assert profile["leg_value_gap_chf"] > 0


def test_refresh_municipality_missing_reporter_row_preserves_profile(monkeypatch):
    """Healthy Energie Reporter bulk result without a row for BFS 261.

    The single-municipality refresh must keep the existing profile's
    source-owned fields field-for-field and report a distinct missing-row
    outcome instead of silently treating the bulk hit as success.
    """
    saved_profiles, _saved_sonnendach = _patch_profile_repository(monkeypatch)
    monkeypatch.setattr(
        public_data,
        "fetch_energie_reporter",
        lambda: [
            {"bfs_number": 1002, "name": "Andere Gemeinde", "kanton": "ZH"},
        ],
    )

    result = public_data.refresh_municipality(261, year=2026)

    assert result["sources"]["energie_reporter"] == "missing_row"
    assert len(saved_profiles) == 1
    profile = saved_profiles[0]
    for field in (
        "name",
        "kanton",
        "population",
        "solar_potential_pct",
        "ev_share_pct",
        "renewable_heating_pct",
        "electricity_consumption_mwh",
        "renewable_production_mwh",
        "energy_transition_score",
    ):
        assert profile[field] == _EXISTING_PROFILE[field]


def test_refresh_municipality_reporter_outage_preserves_fields_and_applies_sonnendach(
    monkeypatch,
):
    """Energie Reporter outage (None) while Sonnendach returns a valid BFS 261 row.

    The single-municipality refresh must report the outage as fetch_failed
    (distinct from the slice-1 missing_row outcome), keep the existing
    profile's reporter-owned fields and score, and update solar_installed_kwp
    from the Sonnendach row.
    """
    saved_profiles, _saved_sonnendach = _patch_profile_repository(monkeypatch)
    solar = {"bfs_number": 261, "potential_kwp": 480.0}
    monkeypatch.setattr(public_data, "fetch_energie_reporter", lambda: None)
    monkeypatch.setattr(public_data, "fetch_sonnendach_municipal", lambda: [solar])

    result = public_data.refresh_municipality(261, year=2026)

    assert result["sources"]["energie_reporter"] == "fetch_failed"
    assert result["sources"]["sonnendach"] == "ok"
    assert len(saved_profiles) == 1
    profile = saved_profiles[0]
    for field in (
        "name",
        "kanton",
        "population",
        "solar_potential_pct",
        "ev_share_pct",
        "renewable_heating_pct",
        "electricity_consumption_mwh",
        "renewable_production_mwh",
        "energy_transition_score",
    ):
        assert profile[field] == _EXISTING_PROFILE[field]
    assert profile["solar_installed_kwp"] == 480.0


def test_refresh_municipality_all_sources_outage_skips_persistence(monkeypatch):
    """Energie Reporter outage, Sonnendach outage, empty ElCom tariff list.

    With every source unavailable the single-municipality refresh must
    report both bulk-source fetch failures, write no profile through the
    database adapter, and distinctly report that persistence was skipped.
    """
    saved_profiles, _saved_sonnendach = _patch_profile_repository(monkeypatch)
    monkeypatch.setattr(public_data, "fetch_energie_reporter", lambda: None)
    monkeypatch.setattr(public_data, "fetch_sonnendach_municipal", lambda: None)

    result = public_data.refresh_municipality(261, year=2026)

    assert result["sources"]["energie_reporter"] == "fetch_failed"
    assert result["sources"]["sonnendach"] == "fetch_failed"
    assert saved_profiles == []
    assert result["persistence"] == "skipped"


def test_refresh_municipality_reporter_row_applies_values_and_reports_ok(monkeypatch):
    """Healthy Energie Reporter bulk containing a fixed BFS 261 row, healthy-empty
    Sonnendach, and no ElCom tariffs.

    The single-municipality refresh must report the Reporter outcome as ok,
    save the row's source-owned values into the profile, derive the energy
    transition score from those values, and keep the stored Sonnendach-owned
    solar_installed_kwp.
    """
    saved_profiles, _saved_sonnendach = _patch_profile_repository(monkeypatch)
    reporter = {
        "bfs_number": 261,
        "name": "Reporter Gemeinde",
        "kanton": "ZH",
        "population": 14000,
        "solar_potential_pct": 50.0,
        "ev_share_pct": 15.0,
        "renewable_heating_pct": 64.0,
        "electricity_consumption_mwh": 1100.0,
        "renewable_production_mwh": 550.0,
    }
    monkeypatch.setattr(public_data, "fetch_energie_reporter", lambda: [reporter])

    result = public_data.refresh_municipality(261, year=2026)

    assert result["sources"]["energie_reporter"] == "ok"
    assert len(saved_profiles) == 1
    profile = saved_profiles[0]
    for field in (
        "name",
        "kanton",
        "population",
        "solar_potential_pct",
        "ev_share_pct",
        "renewable_heating_pct",
        "electricity_consumption_mwh",
        "renewable_production_mwh",
    ):
        assert profile[field] == reporter[field]
    assert profile["energy_transition_score"] == 53.5
    assert profile["solar_installed_kwp"] == _EXISTING_PROFILE["solar_installed_kwp"]


def test_refresh_municipality_empty_bulk_results_preserve_profile(monkeypatch):
    saved_profiles, _saved_sonnendach = _patch_profile_repository(monkeypatch)
    monkeypatch.setattr(public_data, "fetch_energie_reporter", list)

    result = public_data.refresh_municipality(261, year=2026)

    assert result["sources"]["energie_reporter"] == "empty"
    assert result["sources"]["sonnendach"] == "empty"
    assert len(saved_profiles) == 1
    profile = saved_profiles[0]
    for field in (
        "name",
        "kanton",
        "population",
        "solar_potential_pct",
        "solar_installed_kwp",
        "ev_share_pct",
        "renewable_heating_pct",
        "electricity_consumption_mwh",
        "renewable_production_mwh",
        "energy_transition_score",
    ):
        assert profile[field] == _EXISTING_PROFILE[field]


def test_refresh_municipality_empty_tariffs_report_elcom_empty(monkeypatch):
    """Healthy-empty ElCom result must surface as an explicit empty outcome."""
    saved_profiles, _saved_sonnendach = _patch_profile_repository(monkeypatch)
    monkeypatch.setattr(public_data, "fetch_energie_reporter", list)

    result = public_data.refresh_municipality(261, year=2026)

    assert result["sources"]["elcom"] == "empty"
    assert len(saved_profiles) == 1


def test_refresh_municipality_elcom_outage_reports_fetch_failed(monkeypatch):
    """ElCom adapter failure (None) must surface as fetch_failed, not empty."""
    saved_profiles, _saved_sonnendach = _patch_profile_repository(monkeypatch)
    monkeypatch.setattr(public_data, "fetch_energie_reporter", list)
    monkeypatch.setattr(
        public_data, "fetch_elcom_tariffs", lambda _bfs, year=2026: None
    )

    result = public_data.refresh_municipality(261, year=2026)

    assert result["sources"]["elcom"] == "fetch_failed"
    assert len(saved_profiles) == 1


def test_refresh_municipality_empty_sources_flags_false_and_marker_kept(monkeypatch):
    """Healthy-empty Energie Reporter, Sonnendach, and ElCom results.

    The saved profile must keep unrelated data_sources markers, record all
    three source flags as false for this run, and write a parseable UTC
    last_refresh timestamp.
    """
    saved_profiles = []
    monkeypatch.setattr(public_data, "fetch_energie_reporter", list)
    monkeypatch.setattr(public_data, "fetch_sonnendach_municipal", list)
    monkeypatch.setattr(public_data, "fetch_elcom_tariffs", lambda _bfs, year=2026: [])
    monkeypatch.setattr(database, "save_elcom_tariffs", lambda _rows: 0)
    monkeypatch.setattr(
        database,
        "get_municipality_profile",
        lambda _bfs: {
            "bfs_number": 261,
            "name": "Marker Gemeinde",
            "kanton": "ZH",
            "population": 100,
            "energy_transition_score": 42.0,
            "data_sources": {
                "unrelated_marker": "keep-me",
                "elcom": True,
                "energie_reporter": True,
                "sonnendach": True,
            },
        },
    )
    monkeypatch.setattr(
        database,
        "save_municipality_profile",
        lambda profile: saved_profiles.append(profile) or True,
    )

    result = public_data.refresh_municipality(261, year=2026)

    assert result["sources"]["energie_reporter"] == "empty"
    assert result["sources"]["sonnendach"] == "empty"
    assert len(saved_profiles) == 1
    sources = saved_profiles[0]["data_sources"]
    assert sources["unrelated_marker"] == "keep-me"
    assert sources["elcom"] is False
    assert sources["energie_reporter"] is False
    assert sources["sonnendach"] is False
    last_refresh = datetime.fromisoformat(sources["last_refresh"])
    assert last_refresh.tzinfo is not None
    assert last_refresh.utcoffset() == timedelta(0)
