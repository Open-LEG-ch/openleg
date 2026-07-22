# SPDX-License-Identifier: AGPL-3.0-or-later
"""Seam contract for municipality_profile.py (issue #209).

Pins the deep-module interface for profile_context/pilot_context/value_gap
before the module exists. Loaded lazily inside each test so a missing module
produces an assertion failure, not a collection error for the whole suite.
"""

import importlib
import os

import public_data

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE_PATH = os.path.join(PROJECT_ROOT, "municipality_profile.py")

PROFILE = {"bfs_number": 4021, "name": "Baden", "kanton": "AG"}

H4_TARIFF = {
    "bfs_number": 4021,
    "year": 2026,
    "operator_name": "Regionalwerke AG Baden",
    "category": "H4",
    "total_rp_kwh": 27.5,
    "energy_rp_kwh": 12.0,
    "grid_rp_kwh": 9.5,
}

PROFILE_CONTEXT_KEYS = {
    "profile",
    "leg_entries",
    "tariffs",
    "solar",
    "value_gap",
    "h4_tariff",
    "solar_score",
    "solar_over_100",
    "league_chips",
    "improvement",
    "already_top",
    "leaders",
    "site_url",
    "share_base",
    "canonical_url",
    "seo_title",
    "seo_description",
    "jsonld",
    "pilot_slug",
}

PILOT_CONTEXT_KEYS = {
    "profile",
    "bfs",
    "slug",
    "h4",
    "solar",
    "value_gap",
    "json_ld",
    "site_url",
    "canonical_path",
}


def _load_module():
    assert os.path.exists(MODULE_PATH), (
        "municipality_profile.py must exist as the deep module owning "
        "profile/pilot assembly (issue #209)"
    )
    import municipality_profile

    return importlib.reload(municipality_profile)


def _patch_profile_deps(monkeypatch, mp, profile, tariffs, solar=None, registry=None):
    monkeypatch.setattr(mp.db, "get_municipality_profile", lambda bfs: profile)
    monkeypatch.setattr(mp.db, "get_elcom_tariffs", lambda *a, **k: tariffs)
    monkeypatch.setattr(mp.db, "get_sonnendach_municipal", lambda bfs: solar)
    monkeypatch.setattr(mp.db, "list_registry_entries", lambda **k: registry or [])


# === profile_context ===


def test_profile_context_key_set_matches_existing_template_contract(monkeypatch):
    mp = _load_module()
    _patch_profile_deps(monkeypatch, mp, PROFILE, [H4_TARIFF])

    ctx = mp.profile_context(4021, site_url="http://openleg.ch/")

    assert ctx is not None
    assert len(PROFILE_CONTEXT_KEYS) == 19
    assert set(ctx.keys()) == PROFILE_CONTEXT_KEYS
    assert ctx["profile"] == PROFILE
    assert ctx["h4_tariff"] == H4_TARIFF
    assert ctx["pilot_slug"] == "baden"
    assert ctx["site_url"] == "http://openleg.ch"
    assert ctx["canonical_url"] == "http://openleg.ch/gemeinde/profil/4021"


def test_profile_context_tariff_query_uses_year_2026(monkeypatch):
    mp = _load_module()
    calls = []

    def _spy(bfs, year=None):
        calls.append({"bfs": bfs, "year": year})
        return [H4_TARIFF]

    monkeypatch.setattr(mp.db, "get_municipality_profile", lambda bfs: PROFILE)
    monkeypatch.setattr(mp.db, "get_elcom_tariffs", _spy)
    monkeypatch.setattr(mp.db, "get_sonnendach_municipal", lambda bfs: None)
    monkeypatch.setattr(mp.db, "list_registry_entries", lambda **k: [])

    mp.profile_context(4021, site_url="http://openleg.ch")

    assert calls == [{"bfs": 4021, "year": 2026}]


def test_profile_context_missing_profile_returns_none(monkeypatch):
    mp = _load_module()
    monkeypatch.setattr(mp.db, "get_municipality_profile", lambda bfs: None)

    assert mp.profile_context(4021, site_url="http://openleg.ch") is None


# === pilot_context ===


def test_pilot_context_key_set_matches_existing_template_contract(monkeypatch):
    mp = _load_module()
    _patch_profile_deps(monkeypatch, mp, PROFILE, [H4_TARIFF])

    ctx = mp.pilot_context("baden", site_url="http://openleg.ch")

    assert ctx is not None
    assert len(PILOT_CONTEXT_KEYS) == 9
    assert set(ctx.keys()) == PILOT_CONTEXT_KEYS
    assert ctx["bfs"] == 4021
    assert ctx["slug"] == "baden"
    assert ctx["site_url"] == "http://openleg.ch"
    assert ctx["canonical_path"] == "/pilotgemeinde/baden"


def test_pilot_context_tariff_query_is_unfiltered_and_first_h4_wins(monkeypatch):
    mp = _load_module()
    monkeypatch.setattr(mp.db, "get_municipality_profile", lambda bfs: PROFILE)
    monkeypatch.setattr(mp.db, "get_sonnendach_municipal", lambda bfs: None)

    non_h4 = {**H4_TARIFF, "category": "H1"}
    first_h4 = {**H4_TARIFF, "category": "H4", "total_rp_kwh": 30.0}
    second_h4 = {**H4_TARIFF, "category": "H4", "total_rp_kwh": 99.0}
    calls = []

    def _spy(bfs, year=None):
        calls.append({"bfs": bfs, "year": year})
        return [non_h4, first_h4, second_h4]

    monkeypatch.setattr(mp.db, "get_elcom_tariffs", _spy)

    ctx = mp.pilot_context("baden", site_url="http://openleg.ch")

    assert calls == [{"bfs": 4021, "year": None}]
    assert ctx["h4"]["total_rp_kwh"] == 30.0


def test_pilot_context_unknown_slug_returns_none(monkeypatch):
    mp = _load_module()

    assert mp.pilot_context("atlantis", site_url="http://openleg.ch") is None


def test_pilot_context_missing_profile_returns_none(monkeypatch):
    mp = _load_module()
    monkeypatch.setattr(mp.db, "get_municipality_profile", lambda bfs: None)

    assert mp.pilot_context("baden", site_url="http://openleg.ch") is None


# === value_gap ===


def test_value_gap_uses_requested_year_and_grid_reduction(monkeypatch):
    mp = _load_module()
    calls = []

    def _spy(bfs, year=None):
        calls.append({"bfs": bfs, "year": year})
        return [H4_TARIFF]

    monkeypatch.setattr(mp.db, "get_elcom_tariffs", _spy)

    result = mp.value_gap(4021, year=2025, grid_reduction_pct=25.0)

    assert calls == [{"bfs": 4021, "year": 2025}]
    expected = public_data.compute_leg_value_gap(H4_TARIFF, grid_reduction_pct=25.0)
    assert result == expected


def test_value_gap_without_h4_returns_none(monkeypatch):
    mp = _load_module()
    monkeypatch.setattr(mp.db, "get_elcom_tariffs", lambda *a, **k: [])

    assert mp.value_gap(4021) is None
