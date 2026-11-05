# SPDX-License-Identifier: AGPL-3.0-or-later
"""Keep signed evidence intact when replacement or submission cannot commit."""

import pytest
from psycopg2.errors import StringDataRightTruncation

from tests import test_sandbox_http
from tests.test_sandbox_formation import _signed
from tests.test_sandbox_formation_recovery import (
    _add_operator,
    _documents,
    _native_factory,
)

village = test_sandbox_http.village
_csrf = test_sandbox_http._csrf
pytestmark = pytest.mark.integration


def test_failed_replacement_rolls_back_history_and_partial_inserts(
    village, monkeypatch
):
    """Roll back superseding and the first insert when a later insert fails."""
    import database
    from store.formation_signatures import participant_manifest

    client = village.test_client()
    prefix, cid = _signed(client)
    original = _documents(village, client, cid)
    _add_operator(client, prefix, cid)
    connect = _native_factory(village, prefix)
    monkeypatch.setattr(database, "get_connection", connect)
    with connect() as connection, connection.cursor() as cursor:
        manifest = participant_manifest(cursor, cid)
    documents = [
        {
            "doc_type": "gemeinschaftsvereinbarung"
            if index == 0
            else "teilnehmervertrag",
            "filename": "contract.pdf" if index != 1 else "x" * 256,
            "pdf_data": original[0][2],
            "participant_manifest": manifest,
        }
        for index in range(len(manifest) + 1)
    ]
    with pytest.raises(StringDataRightTruncation):
        database.replace_leg_document_bundle(cid, documents)
    assert _documents(village, client, cid) == original
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT status FROM communities WHERE community_id=%s", (cid,))
        assert cursor.fetchone()["status"] == "signatures_pending"
        cursor.execute(
            "SELECT count(*) AS total FROM leg_signature_receipts WHERE community_id=%s",
            (cid,),
        )
        assert cursor.fetchone()["total"] == 3


def test_replacement_rejects_an_incoming_manifest_rendered_before_confirmation(
    village, monkeypatch
):
    """Reject rendered documents that omit a newly confirmed participant."""
    import database

    client = village.test_client()
    prefix, cid = _signed(client)
    original = _documents(village, client, cid)
    connect = _native_factory(village, prefix)
    monkeypatch.setattr(database, "get_connection", connect)
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT doc_type, filename, pdf_data, participant_manifest FROM leg_documents WHERE community_id=%s ORDER BY id",
            (cid,),
        )
        stale_documents = [dict(row) for row in cursor.fetchall()]
    _add_operator(client, prefix, cid)
    with pytest.raises(ValueError):
        database.replace_leg_document_bundle(cid, stale_documents)
    assert _documents(village, client, cid) == original


def test_prepared_manual_submission_protects_its_signed_bundle(village, monkeypatch):
    """Keep a prepared manual package bound to its original signed documents."""
    import database
    import vnb_exchange

    client = village.test_client()
    prefix, cid = _signed(client)
    monkeypatch.setattr(database, "get_connection", _native_factory(village, prefix))
    outcome = vnb_exchange.submit_formation(
        vnb_exchange.FormationSubmission(cid, "sonnwil-founder"),
        vnb_exchange.ManualHandoverAdapter(),
    )
    assert outcome.state == "prepared" and bytes(outcome.manual_package).startswith(
        b"PK"
    )
    original = _documents(village, client, cid)
    _add_operator(client, prefix, cid)
    page = client.get(prefix + "/leg/dashboard?cid=" + cid)
    assert (
        client.post(
            prefix + f"/leg/community/{cid}/documents", data={"csrf_token": _csrf(page)}
        ).status_code
        == 302
    )
    assert _documents(village, client, cid) == original
    assert database.get_vnb_submission_case(cid, outcome.case_id)["state"] == "prepared"
