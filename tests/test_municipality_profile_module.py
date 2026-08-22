# SPDX-License-Identifier: AGPL-3.0-or-later
"""Seam contract for municipality_profile.py (issue #209).

Pins the deep-module interface for value_gap
before the module exists. Loaded lazily inside each test so a missing module
produces an assertion failure, not a collection error for the whole suite.
"""

import importlib
import os

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
