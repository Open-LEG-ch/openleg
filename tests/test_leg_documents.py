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
import formation_documents as formation_documents_module

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
        formation_documents_module.formation_wizard,
        "get_community_status",
        MagicMock(return_value=dict(STATUS)),
    )
    pv_map = pv_map or {"b-admin": 9.5}
    monkeypatch.setattr(
        formation_documents_module.db,
        "get_building_for_dashboard",
        MagicMock(side_effect=lambda bid: {"potential_pv_kwp": pv_map.get(bid, 0)}),
    )
    mock_agreement = MagicMock(return_value=b"%PDF-agreement")
    mock_contract = MagicMock(return_value=b"%PDF-contract")
    monkeypatch.setattr(
        formation_documents_module.document_generator,
        "generate_gemeinschaftsvereinbarung",
        mock_agreement,
    )
    monkeypatch.setattr(
        formation_documents_module.document_generator,
        "generate_teilnehmervertrag",
        mock_contract,
    )
    mock_store = MagicMock(return_value=3)
    monkeypatch.setattr(
        formation_documents_module.db,
        "replace_leg_document_bundle",
        mock_store,
        raising=False,
    )
    return mock_agreement, mock_contract, mock_store


def test_generate_documents_requires_admin(monkeypatch):
    mock_agreement, _, mock_store = _patch(monkeypatch)
    result = dashboard_module.leg_generate_documents("c0ffee", "b-2")
    assert result["error"]
    mock_agreement.assert_not_called()
    mock_store.assert_not_called()


def test_generate_documents_stores_agreement_and_contracts(monkeypatch):
    mock_agreement, mock_contract, mock_store = _patch(monkeypatch)
    result = dashboard_module.leg_generate_documents("c0ffee", "b-admin")
    assert result["error"] is None
    # one agreement + one contract per CONFIRMED member (2 confirmed)
    assert mock_agreement.call_count == 1
    assert mock_contract.call_count == 2
    mock_store.assert_called_once()
    assert len(mock_store.call_args.args[1]) == 3
    # only confirmed members appear as participants
    _, kwargs = mock_agreement.call_args
    participants = kwargs["participants"]
    assert len(participants) == 2
    # the admin building has PV -> producer role
    roles = {p["name"]: p["role"] for p in participants}
    assert roles["a@example.ch"] == "producer"
    assert roles["b@example.ch"] == "consumer"


def test_generate_documents_surfaces_generator_error(monkeypatch):
    mock_agreement, _, mock_store = _patch(monkeypatch, pv_map={})
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
    with open(
        os.path.join(PROJECT_ROOT, "dashboard_routes.py"), encoding="utf-8"
    ) as handle:
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


# --- Regressions found driving the real app (RealDictCursor compat) ---


class _DictCursor:
    def __init__(self, one=None):
        self.one = one
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchone(self):
        return self.one

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _DictConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def test_store_leg_document_returns_id_with_dict_rows(monkeypatch):
    # get_connection uses RealDictCursor: fetchone() returns a dict, so
    # row[0] raised KeyError and rolled back the insert.
    from contextlib import contextmanager

    import database

    cur = _DictCursor(one={"id": 42})

    @contextmanager
    def _conn():
        yield _DictConnection(cur)

    monkeypatch.setattr(database, "get_connection", _conn)
    assert database.store_leg_document("c0ffee", "t", b"%PDF", "f.pdf") == 42


def test_replace_document_bundle_uses_one_transaction(monkeypatch):
    from contextlib import contextmanager

    import store.formation_documents as repository

    cur = _DictCursor(one={"has_signed": False})

    @contextmanager
    def _conn():
        yield _DictConnection(cur)

    monkeypatch.setattr(repository, "_get_connection", _conn)
    documents = [
        {
            "doc_type": "gemeinschaftsvereinbarung",
            "filename": "vereinbarung.pdf",
            "pdf_data": b"%PDF-1",
        },
        {
            "doc_type": "teilnehmervertrag",
            "filename": "vertrag.pdf",
            "pdf_data": b"%PDF-2",
        },
    ]

    assert repository.replace_leg_document_bundle("c0ffee", documents) == 2
    statements = [" ".join(query.split()) for query, _ in cur.executed]
    assert any("DELETE FROM leg_documents" in query for query in statements)
    assert sum("INSERT INTO leg_documents" in query for query in statements) == 2
    assert any("UPDATE communities" in query for query in statements)


# --- Correspondence ledger (Phase 6 MVP) ---


def test_leg_log_correspondence_requires_membership(monkeypatch):
    monkeypatch.setattr(
        dashboard_module.formation_wizard,
        "get_community_status",
        MagicMock(return_value=dict(STATUS)),
    )
    mock_log = MagicMock(return_value=1)
    monkeypatch.setattr(dashboard_module.db, "log_correspondence", mock_log)

    result = dashboard_module.leg_log_correspondence(
        "c0ffee", "b-stranger", "out", "email", "VNB", "Anfrage", ""
    )
    assert result["error"]
    mock_log.assert_not_called()


def test_leg_log_correspondence_as_member(monkeypatch):
    monkeypatch.setattr(
        dashboard_module.formation_wizard,
        "get_community_status",
        MagicMock(return_value=dict(STATUS)),
    )
    mock_log = MagicMock(return_value=1)
    monkeypatch.setattr(dashboard_module.db, "log_correspondence", mock_log)

    result = dashboard_module.leg_log_correspondence(
        "c0ffee", "b-2", "in", "post", "Regionalwerke", "Antwort VNB", "Brief"
    )
    assert result["error"] is None
    _, kwargs = mock_log.call_args
    assert kwargs["community_id"] == "c0ffee"
    assert kwargs["direction"] == "in"
    assert kwargs["channel"] == "post"
    assert kwargs["logged_by"] == "b-2"


def test_leg_overview_includes_correspondence(monkeypatch):
    monkeypatch.setattr(
        dashboard_module.formation_wizard,
        "get_community_status",
        MagicMock(return_value=dict(STATUS)),
    )
    monkeypatch.setattr(
        dashboard_module.db, "list_leg_documents", MagicMock(return_value=[])
    )
    monkeypatch.setattr(
        dashboard_module.db,
        "list_correspondence",
        MagicMock(return_value=[{"id": 1, "subject": "Antwort VNB"}]),
    )
    result = dashboard_module.leg_overview("c0ffee", "b-admin")
    assert result["correspondence"] == [{"id": 1, "subject": "Antwort VNB"}]


def test_correspondence_route_in_source():
    with open(
        os.path.join(PROJECT_ROOT, "dashboard_routes.py"), encoding="utf-8"
    ) as handle:
        source = handle.read()
    assert '"/leg/community/<community_id>/correspondence"' in source


def test_dashboard_template_has_correspondence_section():
    path = os.path.join(PROJECT_ROOT, "templates", "leg_dashboard.html")
    with open(path, encoding="utf-8") as handle:
        html = handle.read()
    assert "/correspondence" in html
    assert "Korrespondenz" in html
    assert "correspondence" in html
