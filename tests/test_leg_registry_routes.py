# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the public LEG registry routes (/leg-verzeichnis)."""

import os
from unittest.mock import MagicMock

from flask import Flask

import leg_registry as leg_registry_module

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SAMPLE_ENTRY = {
    "id": 1,
    "slug": "leg-baden",
    "name": "LEG Baden",
    "kanton": "AG",
    "plz": "5400",
    "ort": "Baden",
    "vnb_name": "Regionalwerke Baden",
    "leg_status": "aktiv",
    "member_count_estimate": 12,
    "description": "Nachbarschaftliche Stromgemeinschaft in Baden.",
    "website_url": "",
    "contact_email": "info@example.ch",
    "moderation_status": "published",
}


def _make_client(monkeypatch, list_return=None, detail_return=None):
    mock_list = MagicMock(return_value=list_return if list_return is not None else [])
    mock_detail = MagicMock(return_value=detail_return)
    monkeypatch.setattr(leg_registry_module.db, "list_registry_entries", mock_list)
    monkeypatch.setattr(
        leg_registry_module.db, "get_registry_entry_by_slug", mock_detail
    )
    app = Flask(__name__, template_folder=os.path.join(PROJECT_ROOT, "templates"))
    app.config["TESTING"] = True
    app.register_blueprint(leg_registry_module.registry_bp)
    return app.test_client(), mock_list, mock_detail


def test_liste_renders_published_entries(monkeypatch):
    client, mock_list, _ = _make_client(monkeypatch, list_return=[SAMPLE_ENTRY])
    resp = client.get("/leg-verzeichnis")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="ignore")
    assert "LEG Baden" in html
    assert "Baden" in html


def test_liste_never_passes_client_supplied_moderation_status(monkeypatch):
    # A malicious query string must not be able to widen what's listed;
    # the route must not accept/forward a moderation_status override at all.
    client, mock_list, _ = _make_client(monkeypatch, list_return=[])
    client.get("/leg-verzeichnis?moderation_status=pending")
    _, kwargs = mock_list.call_args
    assert "moderation_status" not in kwargs


def test_liste_filters_by_kanton_plz_and_status(monkeypatch):
    client, mock_list, _ = _make_client(monkeypatch, list_return=[])
    client.get("/leg-verzeichnis?kanton=ag&plz=5400&leg_status=aktiv&q=Baden")
    _, kwargs = mock_list.call_args
    assert kwargs["kanton"] == "AG"
    assert kwargs["plz"] == "5400"
    assert kwargs["leg_status"] == "aktiv"
    assert kwargs["q"] == "Baden"


def test_liste_states_no_grid_topology_verification():
    app = Flask(__name__, template_folder=os.path.join(PROJECT_ROOT, "templates"))
    app.config["TESTING"] = True
    app.register_blueprint(leg_registry_module.registry_bp)
    import unittest.mock as mock

    with mock.patch.object(
        leg_registry_module.db, "list_registry_entries", return_value=[]
    ):
        html = app.test_client().get("/leg-verzeichnis").data.decode("utf-8")
    assert "prüft keine Netz-Topologie" in html


def test_detail_renders_published_entry(monkeypatch):
    client, _, mock_detail = _make_client(monkeypatch, detail_return=SAMPLE_ENTRY)
    resp = client.get("/leg-verzeichnis/leg-baden")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="ignore")
    assert "LEG Baden" in html
    assert "prüft keine Netz-Topologie" in html
    mock_detail.assert_called_once_with("leg-baden")


def test_detail_404s_for_unknown_slug(monkeypatch):
    client, _, _ = _make_client(monkeypatch, detail_return=None)
    resp = client.get("/leg-verzeichnis/nope")
    assert resp.status_code == 404


def test_detail_404s_for_unpublished_entry(monkeypatch):
    pending_entry = {**SAMPLE_ENTRY, "moderation_status": "pending"}
    client, _, _ = _make_client(monkeypatch, detail_return=pending_entry)
    resp = client.get("/leg-verzeichnis/leg-baden")
    assert resp.status_code == 404


# --- Self-service submission ---


def _submit_client(monkeypatch, save_return=None):
    mock_save = MagicMock(
        return_value=save_return if save_return is not None else {"id": 1}
    )
    monkeypatch.setattr(leg_registry_module.db, "save_registry_entry", mock_save)
    monkeypatch.setattr(
        leg_registry_module.db,
        "get_registry_entry_by_slug",
        MagicMock(return_value=None),
    )
    mock_track = MagicMock(return_value=True)
    monkeypatch.setattr(leg_registry_module.db, "track_event", mock_track)
    app = Flask(__name__, template_folder=os.path.join(PROJECT_ROOT, "templates"))
    app.config["TESTING"] = True
    app.register_blueprint(leg_registry_module.registry_bp)
    return app.test_client(), mock_save, mock_track


def test_eintragen_get_renders_form_with_honesty_boundary(monkeypatch):
    client, _, _ = _submit_client(monkeypatch)
    resp = client.get("/leg-verzeichnis/eintragen")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="ignore")
    assert "prüft keine Netz-Topologie" in html
    assert "manuell" in html.lower() or "geprüft" in html.lower()


def test_eintragen_post_creates_pending_self_submitted_entry(monkeypatch):
    client, mock_save, mock_track = _submit_client(monkeypatch)
    resp = client.post(
        "/leg-verzeichnis/eintragen",
        data={
            "name": "LEG Baden",
            "contact_email": "info@example.ch",
            "kanton": "AG",
            "plz": "5400",
            "ort": "Baden",
        },
    )
    assert resp.status_code in (200, 302)
    assert mock_save.called
    _, kwargs = mock_save.call_args
    assert kwargs["name"] == "LEG Baden"
    assert kwargs["contact_email"] == "info@example.ch"
    assert mock_track.called


def test_eintragen_post_rejects_missing_required_fields(monkeypatch):
    client, mock_save, _ = _submit_client(monkeypatch)
    resp = client.post(
        "/leg-verzeichnis/eintragen",
        data={"kanton": "AG"},
    )
    assert resp.status_code == 400
    assert not mock_save.called


def test_eintragen_post_rejects_invalid_email(monkeypatch):
    client, mock_save, _ = _submit_client(monkeypatch)
    resp = client.post(
        "/leg-verzeichnis/eintragen",
        data={"name": "LEG Baden", "contact_email": "not-an-email"},
    )
    assert resp.status_code == 400
    assert not mock_save.called


def test_eintragen_post_generates_slug_from_name(monkeypatch):
    client, mock_save, _ = _submit_client(monkeypatch)
    client.post(
        "/leg-verzeichnis/eintragen",
        data={"name": "LEG Müswangen-Süd", "contact_email": "info@example.ch"},
    )
    _, kwargs = mock_save.call_args
    assert kwargs["slug"]
    assert " " not in kwargs["slug"]
    assert kwargs["slug"] == kwargs["slug"].lower()


# --- Claim flow ---


def _claim_client(monkeypatch, entry=None, claim_lookup=None):
    monkeypatch.setattr(
        leg_registry_module.db,
        "get_registry_entry_by_slug",
        MagicMock(return_value=entry),
    )
    mock_set_token = MagicMock(return_value=True)
    monkeypatch.setattr(
        leg_registry_module.db, "set_registry_claim_token", mock_set_token
    )
    mock_send = MagicMock(return_value=True)
    monkeypatch.setattr(leg_registry_module.email_utils, "send_email", mock_send)
    mock_lookup = MagicMock(return_value=claim_lookup)
    monkeypatch.setattr(
        leg_registry_module.db, "get_registry_entry_by_claim_token", mock_lookup
    )
    mock_mark = MagicMock(return_value=True)
    monkeypatch.setattr(
        leg_registry_module.db, "mark_registry_entry_claimed", mock_mark
    )
    app = Flask(__name__, template_folder=os.path.join(PROJECT_ROOT, "templates"))
    app.config["TESTING"] = True
    app.register_blueprint(leg_registry_module.registry_bp)
    return app.test_client(), mock_set_token, mock_send, mock_mark


def test_claim_request_get_renders_confirm_page(monkeypatch):
    client, _, _, _ = _claim_client(monkeypatch, entry=SAMPLE_ENTRY)
    resp = client.get("/leg-verzeichnis/leg-baden/beanspruchen")
    assert resp.status_code == 200
    assert "LEG Baden" in resp.data.decode("utf-8", errors="ignore")


def test_claim_request_get_404s_for_unknown_slug(monkeypatch):
    client, _, _, _ = _claim_client(monkeypatch, entry=None)
    resp = client.get("/leg-verzeichnis/nope/beanspruchen")
    assert resp.status_code == 404


def test_claim_request_post_generates_token_and_sends_email(monkeypatch):
    client, mock_set_token, mock_send, _ = _claim_client(
        monkeypatch, entry=SAMPLE_ENTRY
    )
    resp = client.post("/leg-verzeichnis/leg-baden/beanspruchen")
    assert resp.status_code == 200
    assert mock_set_token.called
    args, _ = mock_set_token.call_args
    assert args[0] == SAMPLE_ENTRY["id"]
    assert mock_send.called
    send_args, send_kwargs = mock_send.call_args
    to_email = send_args[0] if send_args else send_kwargs.get("to_email")
    assert to_email == SAMPLE_ENTRY["contact_email"]


def test_claim_confirm_marks_entry_claimed_with_valid_token(monkeypatch):
    client, _, _, mock_mark = _claim_client(monkeypatch, claim_lookup=SAMPLE_ENTRY)
    resp = client.get("/leg-verzeichnis/beanspruchen/validtoken123")
    assert resp.status_code == 200
    mock_mark.assert_called_once_with(SAMPLE_ENTRY["id"], SAMPLE_ENTRY["contact_email"])


def test_claim_confirm_rejects_expired_or_unknown_token(monkeypatch):
    client, _, _, mock_mark = _claim_client(monkeypatch, claim_lookup=None)
    resp = client.get("/leg-verzeichnis/beanspruchen/badtoken")
    assert resp.status_code == 404
    mock_mark.assert_not_called()


# --- Re-confirmation (freshness) ---


def _verify_client(monkeypatch, entry=None):
    mock_lookup = MagicMock(return_value=entry)
    monkeypatch.setattr(
        leg_registry_module.db, "get_registry_entry_by_verification_token", mock_lookup
    )
    mock_mark = MagicMock(return_value=True)
    monkeypatch.setattr(
        leg_registry_module.db, "mark_registry_entry_verified", mock_mark
    )
    app = Flask(__name__, template_folder=os.path.join(PROJECT_ROOT, "templates"))
    app.config["TESTING"] = True
    app.register_blueprint(leg_registry_module.registry_bp)
    return app.test_client(), mock_mark


def test_verify_confirm_marks_entry_verified_with_valid_token(monkeypatch):
    client, mock_mark = _verify_client(monkeypatch, entry=SAMPLE_ENTRY)
    resp = client.get("/leg-verzeichnis/bestaetigen/validtoken123")
    assert resp.status_code == 200
    mock_mark.assert_called_once_with(SAMPLE_ENTRY["id"])


def test_verify_confirm_rejects_expired_or_unknown_token(monkeypatch):
    client, mock_mark = _verify_client(monkeypatch, entry=None)
    resp = client.get("/leg-verzeichnis/bestaetigen/badtoken")
    assert resp.status_code == 404
    mock_mark.assert_not_called()
