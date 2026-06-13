# SPDX-License-Identifier: AGPL-3.0-or-later
"""Organic-growth focused tests for municipality routes."""

import os
from flask import Flask

import municipality as municipality_module


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _make_client():
    app = Flask(__name__, template_folder=os.path.join(PROJECT_ROOT, "templates"))
    app.config["TESTING"] = True
    app.register_blueprint(municipality_module.municipality_bp)
    return app.test_client()


def test_onboarding_renders_typeahead_search():
    client = _make_client()
    resp = client.get("/gemeinde/onboarding")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="ignore")
    assert "Gemeinde suchen" in html
    assert "municipality-search" in html


def test_register_accepts_any_known_bfs(monkeypatch):
    client = _make_client()
    monkeypatch.setattr(
        municipality_module.db,
        "get_municipality_profile",
        lambda bfs: {
            "bfs_number": bfs,
            "name": "Dietikon",
            "kanton": "ZH",
            "population": 29000,
        },
    )
    monkeypatch.setattr(
        municipality_module.security_utils,
        "validate_email_address",
        lambda email: (True, email.strip().lower(), ""),
    )
    monkeypatch.setattr(municipality_module.db, "save_municipality", lambda **kwargs: 1)
    monkeypatch.setattr(
        municipality_module.db,
        "update_municipality_status",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        municipality_module.db, "track_event", lambda *args, **kwargs: True
    )

    resp = client.post(
        "/gemeinde/register",
        json={"bfs_number": 261, "admin_email": "Admin@Dietikon.ch"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["municipality_id"] == 1


def test_register_rejects_unknown_bfs(monkeypatch):
    client = _make_client()
    monkeypatch.setattr(
        municipality_module.db, "get_municipality_profile", lambda _bfs: None
    )
    monkeypatch.setattr(
        municipality_module.security_utils,
        "validate_email_address",
        lambda email: (True, email.strip().lower(), ""),
    )

    resp = client.post(
        "/gemeinde/register",
        json={"bfs_number": 999999, "admin_email": "info@example.ch"},
    )
    assert resp.status_code == 400
    assert "Unbekannte BFS-Nummer" in resp.get_json()["error"]


def test_register_rejects_invalid_email(monkeypatch):
    client = _make_client()
    monkeypatch.setattr(
        municipality_module.security_utils,
        "validate_email_address",
        lambda _email: (False, "", "Ungültige E-Mail"),
    )

    resp = client.post(
        "/gemeinde/register",
        json={"bfs_number": 261, "admin_email": "bad-email"},
    )
    assert resp.status_code == 400
    assert "Ungültige E-Mail" in resp.get_json()["error"]


def test_verzeichnis_defaults_to_all_cantons_and_handles_empty(monkeypatch):
    client = _make_client()
    calls = {}

    def _fake_get_all_municipality_profiles(kanton=None, order_by="name"):
        calls["kanton"] = kanton
        calls["order_by"] = order_by
        return []

    monkeypatch.setattr(
        municipality_module.db,
        "get_all_municipality_profiles",
        _fake_get_all_municipality_profiles,
    )
    resp = client.get("/gemeinde/verzeichnis")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="ignore")
    assert "Keine Gemeinden gefunden" in html
    assert calls["kanton"] is None


def test_verzeichnis_and_profil_render_canonical_from_host(monkeypatch):
    client = _make_client()
    monkeypatch.setattr(
        municipality_module.db, "get_all_municipality_profiles", lambda **_kwargs: []
    )
    monkeypatch.setattr(
        municipality_module.db,
        "get_municipality_profile",
        lambda bfs: {
            "bfs_number": bfs,
            "name": "Dietikon",
            "kanton": "ZH",
            "energy_transition_score": 0,
        },
    )
    monkeypatch.setattr(
        municipality_module.db, "get_elcom_tariffs", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        municipality_module.db, "get_sonnendach_municipal", lambda _bfs: None
    )

    verzeichnis = client.get("/gemeinde/verzeichnis", headers={"Host": "openleg.ch"})
    assert verzeichnis.status_code == 200
    html_verzeichnis = verzeichnis.data.decode("utf-8", errors="ignore")
    assert (
        'rel="canonical" href="http://openleg.ch/gemeinde/verzeichnis"'
        in html_verzeichnis
    )

    profil = client.get("/gemeinde/profil/261", headers={"Host": "openleg.ch"})
    assert profil.status_code == 200
    html_profil = profil.data.decode("utf-8", errors="ignore")
    assert 'rel="canonical" href="http://openleg.ch/gemeinde/profil/261"' in html_profil


def _patch_profil_deps(monkeypatch, profile):
    monkeypatch.setattr(
        municipality_module.db, "get_municipality_profile", lambda _bfs: profile
    )
    monkeypatch.setattr(
        municipality_module.db, "get_elcom_tariffs", lambda *_a, **_k: []
    )
    monkeypatch.setattr(
        municipality_module.db, "get_sonnendach_municipal", lambda _bfs: None
    )


def test_profil_solarnutzung_uses_pv_score(monkeypatch):
    client = _make_client()
    _patch_profil_deps(
        monkeypatch,
        {"bfs_number": 4021, "name": "Baden", "kanton": "AG", "pv_score_pct": 42.7},
    )
    resp = client.get("/gemeinde/profil/4021")
    html = resp.data.decode("utf-8", errors="ignore")
    assert "Solarnutzung" in html
    assert "43%" in html


def test_profil_solarnutzung_caps_over_100(monkeypatch):
    client = _make_client()
    _patch_profil_deps(
        monkeypatch,
        {"bfs_number": 1, "name": "Abtwil", "kanton": "AG", "pv_score_pct": 104.0},
    )
    resp = client.get("/gemeinde/profil/1")
    html = resp.data.decode("utf-8", errors="ignore")
    assert "100%" in html
    assert "Schätzung übertroffen" in html


def test_profil_solarnutzung_falls_back_to_old_metric(monkeypatch):
    client = _make_client()
    _patch_profil_deps(
        monkeypatch,
        {
            "bfs_number": 2,
            "name": "Altdorf",
            "kanton": "UR",
            "pv_score_pct": None,
            "solar_potential_pct": 33.0,
        },
    )
    resp = client.get("/gemeinde/profil/2")
    html = resp.data.decode("utf-8", errors="ignore")
    assert "33%" in html
