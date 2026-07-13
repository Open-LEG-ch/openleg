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
