# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the contract builder wiring (Phase 5).

dashboard.leg_generate_documents wires formation_wizard community state to
document_generator's real PDFs, stored in leg_documents. Admin-only
generation, member-gated download, and a "keine Rechtsberatung"
disclaimer pinned on the dashboard.
"""

import os
from unittest.mock import MagicMock

import dashboard as dashboard_module

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STATUS = {
    "community_id": "c0ffee",
    "name": "LEG Musterweg",
    "status": "formation_started",
    "distribution_model": "proportional",
    "member_count": {"total": 3, "confirmed": 3, "invited": 0},
    "members": [
        {
            "building_id": "b-admin",
            "role": "admin",
            "status": "confirmed",
            "email": "a@example.ch",
            "address": "Musterweg 1",
        },
        {
            "building_id": "b-2",
            "role": "member",
            "status": "confirmed",
            "email": "b@example.ch",
            "address": "Musterweg 3",
        },
        {
            "building_id": "b-3",
            "role": "member",
            "status": "invited",
            "email": "c@example.ch",
            "address": "Musterweg 5",
        },
    ],
    "documents": None,
    "next_steps": [],
}


def _patch(monkeypatch, pv_map=None):
    monkeypatch.setattr(
        dashboard_module.formation_wizard,
        "get_community_status",
        MagicMock(return_value=dict(STATUS)),
    )
    pv_map = pv_map or {"b-admin": 9.5}
    monkeypatch.setattr(
        dashboard_module.db,
        "get_building_for_dashboard",
        MagicMock(side_effect=lambda bid: {"potential_pv_kwp": pv_map.get(bid, 0)}),
    )
    mock_agreement = MagicMock(return_value=b"%PDF-agreement")
    mock_contract = MagicMock(return_value=b"%PDF-contract")
    monkeypatch.setattr(
        dashboard_module.document_generator,
        "generate_gemeinschaftsvereinbarung",
        mock_agreement,
    )
    monkeypatch.setattr(
        dashboard_module.document_generator,
        "generate_teilnehmervertrag",
        mock_contract,
    )
    mock_store = MagicMock(return_value=7)
    monkeypatch.setattr(dashboard_module.db, "store_leg_document", mock_store)
    mock_wizard_docs = MagicMock(return_value={"community_agreement": {}})
    monkeypatch.setattr(
        dashboard_module.formation_wizard, "generate_documents", mock_wizard_docs
    )
    return mock_agreement, mock_contract, mock_store, mock_wizard_docs


def test_generate_documents_requires_admin(monkeypatch):
    mock_agreement, _, mock_store, _ = _patch(monkeypatch)
    result = dashboard_module.leg_generate_documents("c0ffee", "b-2")
    assert result["error"]
    mock_agreement.assert_not_called()
    mock_store.assert_not_called()


def test_generate_documents_stores_agreement_and_contracts(monkeypatch):
    mock_agreement, mock_contract, mock_store, mock_wizard = _patch(monkeypatch)
    result = dashboard_module.leg_generate_documents("c0ffee", "b-admin")
    assert result["error"] is None
    # one agreement + one contract per CONFIRMED member (2 confirmed)
    assert mock_agreement.call_count == 1
    assert mock_contract.call_count == 2
    assert mock_store.call_count == 3
    # only confirmed members appear as participants
    _, kwargs = mock_agreement.call_args
    participants = kwargs["participants"]
    assert len(participants) == 2
    # the admin building has PV -> producer role
    roles = {p["name"]: p["role"] for p in participants}
    assert roles["a@example.ch"] == "producer"
    assert roles["b@example.ch"] == "consumer"
    # wizard status transition ran
    mock_wizard.assert_called_once()


def test_generate_documents_surfaces_generator_error(monkeypatch):
    mock_agreement, _, mock_store, _ = _patch(monkeypatch, pv_map={})
    mock_agreement.side_effect = ValueError(
        "Eine LEG benötigt mindestens einen Produzent"
    )
    result = dashboard_module.leg_generate_documents("c0ffee", "b-admin")
    assert "Produzent" in result["error"]
    mock_store.assert_not_called()


def test_document_download_gated_on_membership(monkeypatch):
    monkeypatch.setattr(
        dashboard_module.formation_wizard,
        "get_community_status",
        MagicMock(return_value=dict(STATUS)),
    )
    doc = {
        "id": 7,
        "community_id": "c0ffee",
        "doc_type": "gemeinschaftsvereinbarung",
        "filename": "vereinbarung.pdf",
        "pdf_data": b"%PDF-agreement",
    }
    monkeypatch.setattr(
        dashboard_module.db, "get_leg_document", MagicMock(return_value=doc)
    )
    ok = dashboard_module.leg_document_for_member(7, "b-2")
    assert ok is not None
    assert ok["pdf_data"] == b"%PDF-agreement"

    denied = dashboard_module.leg_document_for_member(7, "b-stranger")
    assert denied is None


def test_document_routes_in_source():
    with open(os.path.join(PROJECT_ROOT, "app.py"), encoding="utf-8") as handle:
        source = handle.read()
    assert '"/leg/community/<community_id>/documents"' in source
    assert '"/leg/document/<int:doc_id>"' in source


def test_dashboard_template_has_documents_section_with_disclaimer():
    path = os.path.join(PROJECT_ROOT, "templates", "leg_dashboard.html")
    with open(path, encoding="utf-8") as handle:
        html = handle.read()
    assert "/documents" in html
    assert "keine Rechtsberatung" in html
    assert "leg_documents" in html
