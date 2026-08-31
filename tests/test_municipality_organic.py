# SPDX-License-Identifier: AGPL-3.0-or-later
"""Municipality onboarding product contracts."""

from pathlib import Path

from flask import Flask

import municipality

ROOT = Path(__file__).resolve().parents[1]


def _client():
    app = Flask(__name__, template_folder=ROOT / "templates")
    app.config["TESTING"] = True
    app.jinja_env.globals["public_site_url"] = lambda path: f"https://openleg.ch{path}"
    app.register_blueprint(municipality.municipality_bp)
    return app.test_client()


def test_onboarding_renders_accessible_typeahead_form():
    response = _client().get("/gemeinde/onboarding")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    for control_id in ("municipality-search", "admin-email", "contact-name"):
        assert f'id="{control_id}"' in html
        assert f'for="{control_id}"' in html
    assert 'id="form-error" role="alert"' in html
    assert 'id="form-success" role="status"' in html
    assert "fetch('/gemeinde/register'" in html


def test_register_accepts_known_municipality(monkeypatch):
    monkeypatch.setattr(
        municipality.db,
        "get_municipality_profile",
        lambda bfs: {
            "bfs_number": bfs,
            "name": "Dietikon",
            "kanton": "ZH",
            "population": 29000,
        },
    )
    monkeypatch.setattr(
        municipality.security_utils,
        "validate_email_address",
        lambda email: (True, email.strip().lower(), ""),
    )
    monkeypatch.setattr(municipality.db, "save_municipality", lambda **_kwargs: 1)
    monkeypatch.setattr(
        municipality.db, "update_municipality_status", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr(municipality.db, "track_event", lambda *_args, **_kwargs: True)

    response = _client().post(
        "/gemeinde/register",
        json={"bfs_number": 261, "admin_email": "Admin@Dietikon.ch"},
    )

    assert response.status_code == 200
    assert response.get_json()["municipality_id"] == 1


def test_register_rejects_unknown_municipality(monkeypatch):
    monkeypatch.setattr(municipality.db, "get_municipality_profile", lambda _bfs: None)
    monkeypatch.setattr(
        municipality.security_utils,
        "validate_email_address",
        lambda email: (True, email.strip().lower(), ""),
    )

    response = _client().post(
        "/gemeinde/register",
        json={"bfs_number": 999999, "admin_email": "info@example.ch"},
    )

    assert response.status_code == 400
    assert "Unbekannte BFS-Nummer" in response.get_json()["error"]


def test_register_rejects_invalid_email(monkeypatch):
    monkeypatch.setattr(
        municipality.security_utils,
        "validate_email_address",
        lambda _email: (False, "", "Ungültige E-Mail"),
    )

    response = _client().post(
        "/gemeinde/register",
        json={"bfs_number": 261, "admin_email": "bad-email"},
    )

    assert response.status_code == 400
    assert "Ungültige E-Mail" in response.get_json()["error"]


def test_public_municipality_profile_route_is_restored(monkeypatch):
    profile = {
        "bfs_number": 261,
        "name": "Dietikon",
        "kanton": "ZH",
        "energy_transition_score": 0,
        "pv_score_pct": 42.0,
    }
    monkeypatch.setattr(
        municipality.db, "get_municipality_profile", lambda _bfs: profile
    )
    monkeypatch.setattr(
        municipality.db, "get_elcom_tariffs", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(municipality.db, "get_sonnendach_municipal", lambda _bfs: None)
    monkeypatch.setattr(municipality.db, "list_registry_entries", lambda **_kwargs: [])

    response = _client().get("/gemeinde/profil/261")

    assert response.status_code == 200
    assert "Dietikon" in response.get_data(as_text=True)
