# SPDX-License-Identifier: AGPL-3.0-or-later
"""TDD contracts for complete resident and LEG dashboard loops (#292)."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import dashboard
from tests.test_dashboard_access_routes import _set_session
from tests.test_dashboard_access_routes import (  # noqa: F401
    app_module as dashboard_app_module,
)
from tests.test_leg_dashboard import _patch_status


def test_leg_invite_resolves_email_without_exposing_profile_id(monkeypatch):
    _patch_status(monkeypatch)
    monkeypatch.setattr(
        dashboard.db,
        "get_building_by_email",
        MagicMock(return_value=[{"building_id": "b-new"}]),
    )
    invite = MagicMock(return_value=True)
    monkeypatch.setattr(dashboard.formation_wizard, "invite_member", invite)

    result = dashboard.leg_invite_by_email("c0ffee", "b-admin", "person@example.ch")

    assert result == {"error": None}
    invite.assert_called_once_with(dashboard.db, "c0ffee", "b-new", "b-admin")


def test_leg_invite_email_response_is_generic(monkeypatch):
    _patch_status(monkeypatch)
    monkeypatch.setattr(dashboard.db, "get_building_by_email", lambda _email: [])
    missing = dashboard.leg_invite_by_email("c0ffee", "b-admin", "missing@example.ch")
    monkeypatch.setattr(
        dashboard.db,
        "get_building_by_email",
        lambda _email: [{"building_id": "b-admin"}],
    )
    existing = dashboard.leg_invite_by_email("c0ffee", "b-admin", "admin@example.ch")

    assert missing == existing == {"error": None}


def test_invite_route_uses_email_session_and_csrf(
    dashboard_app_module,  # noqa: F811
    monkeypatch,
):
    invite = MagicMock(return_value={"error": None})
    monkeypatch.setattr(
        dashboard_app_module.dashboard_module, "leg_invite_by_email", invite
    )
    client = dashboard_app_module.web.test_client()
    _set_session(client)

    response = client.post(
        "/leg/community/c0ffee/invite",
        data={
            "csrf_token": "csrf-secret",
            "invite_email": "person@example.ch",
            "invite_building_id": "b-attacker",
        },
    )

    assert response.status_code == 302
    invite.assert_called_once_with("c0ffee", "building-session", "person@example.ch")


def test_invite_route_renders_validation_error(
    dashboard_app_module,  # noqa: F811
    monkeypatch,
):
    monkeypatch.setattr(
        dashboard_app_module.dashboard_module,
        "leg_invite_by_email",
        lambda *_args: {"error": "Bitte geben Sie eine gültige E-Mail-Adresse ein."},
    )
    overview = {
        "error": None,
        "community": {
            "community_id": "c0ffee",
            "name": "LEG Musterweg",
            "status": "interested",
            "distribution_model": "simple",
            "readiness_score": 0,
            "member_count": {"confirmed": 1, "total": 1, "invited": 0},
            "members": [],
            "next_steps": [],
        },
        "viewer_building_id": "building-session",
        "is_admin": True,
        "leg_documents": [],
        "correspondence": [],
    }
    monkeypatch.setattr(
        dashboard_app_module.dashboard_module,
        "leg_overview",
        lambda *_args: overview,
    )
    client = dashboard_app_module.web.test_client()
    _set_session(client)

    response = client.post(
        "/leg/community/c0ffee/invite",
        data={"csrf_token": "csrf-secret", "invite_email": "bad"},
    )

    assert response.status_code == 400
    assert "gültige E-Mail-Adresse" in response.get_data(as_text=True)


def test_profile_export_is_session_gated_private_and_json(
    dashboard_app_module,  # noqa: F811
    monkeypatch,
):
    profile = {
        "building_id": "building-session",
        "email": "person@example.ch",
        "address": "Musterweg 1",
        "annual_consumption_kwh": 4200,
    }
    monkeypatch.setattr(
        dashboard_app_module.dashboard_module,
        "export_profile",
        MagicMock(return_value=profile),
    )
    anonymous = dashboard_app_module.web.test_client().get("/dashboard/export")
    assert anonymous.status_code == 401

    client = dashboard_app_module.web.test_client()
    _set_session(client)
    response = client.get("/dashboard/export")

    assert response.status_code == 200
    assert response.mimetype == "application/json"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "attachment" in response.headers["Content-Disposition"]
    assert json.loads(response.get_data()) == profile


def test_profile_export_normalizes_non_json_values(monkeypatch):
    monkeypatch.setattr(
        dashboard.db,
        "get_building",
        lambda _building_id: {
            "building_id": "building-1",
            "annual_consumption_kwh": 4200,
            "private_internal": object(),
        },
    )
    exported = dashboard.export_profile("building-1")

    assert exported["annual_consumption_kwh"] == 4200
    assert "private_internal" not in exported


def test_profile_export_omits_non_finite_numbers(monkeypatch):
    monkeypatch.setattr(
        dashboard.db,
        "get_building",
        lambda _building_id: {
            "building_id": "building-1",
            "lat": float("nan"),
            "annual_consumption_kwh": float("inf"),
        },
    )

    exported = dashboard.export_profile("building-1")

    assert "lat" not in exported
    assert "annual_consumption_kwh" not in exported


def test_dashboard_templates_expose_human_controls_and_progress():
    leg = Path("templates/leg_dashboard.html").read_text(encoding="utf-8")
    resident = Path("templates/dashboard.html").read_text(encoding="utf-8")

    assert 'name="invite_email"' in leg
    assert 'name="invite_building_id"' not in leg
    assert "Profil-ID des Nachbarn" not in leg
    assert "Verantwortlich" in leg
    assert "Nächster Schritt" in leg
    assert "Erwartete Dauer" in leg
    assert 'href="/dashboard/export"' in resident
    assert 'href="/unsubscribe"' in resident
    assert 'name="attachment"' in leg
    assert "Korrekturen bleiben" in leg


def test_correspondence_rejects_non_pdf_attachment(monkeypatch):
    monkeypatch.setattr(
        dashboard.formation_wizard,
        "get_community_status",
        lambda *_args: {"members": [{"building_id": "b-admin", "role": "admin"}]},
    )
    save = MagicMock()
    monkeypatch.setattr(dashboard.db, "log_correspondence", save)

    result = dashboard.leg_log_correspondence(
        "c0ffee",
        "b-admin",
        "in",
        "email",
        "VNB",
        "Antwort",
        attachment_filename="malware.exe",
        attachment_data=b"MZ",
    )

    assert result["error"] == "Anhänge müssen PDF-Dateien sein."
    save.assert_not_called()


def test_correspondence_rejects_empty_selected_pdf(monkeypatch):
    monkeypatch.setattr(
        dashboard.formation_wizard,
        "get_community_status",
        lambda *_args: {"members": [{"building_id": "b-admin"}]},
    )
    save = MagicMock()
    monkeypatch.setattr(dashboard.db, "log_correspondence", save)

    result = dashboard.leg_log_correspondence(
        "c0ffee",
        "b-admin",
        "in",
        "email",
        "VNB",
        "Antwort",
        attachment_filename="leer.pdf",
        attachment_data=b"",
    )

    assert result["error"] == "Der Anhang ist keine gültige PDF-Datei."
    save.assert_not_called()


def test_correspondence_attachment_requires_community_membership(monkeypatch):
    monkeypatch.setattr(
        dashboard.db,
        "get_correspondence_attachment",
        lambda *_args: {
            "community_id": "c0ffee",
            "attachment_filename": "antwort.pdf",
            "attachment_data": b"%PDF",
        },
    )
    monkeypatch.setattr(
        dashboard.formation_wizard,
        "get_community_status",
        lambda *_args: {"members": [{"building_id": "b-admin"}]},
    )

    assert dashboard.leg_correspondence_attachment(4, "c0ffee", "stranger") is None
    assert dashboard.leg_correspondence_attachment(4, "c0ffee", "b-admin")
