# SPDX-License-Identifier: AGPL-3.0-or-later
"""Recover stale local signing bundles without losing signed evidence."""

import re
from contextlib import contextmanager

import psycopg2
import pytest
from psycopg2.extensions import make_dsn
from psycopg2.extras import RealDictCursor

from tests import test_sandbox_http
from tests.test_sandbox_formation import _signed
from tests.test_sandbox_onboarding import _workspace_connection
from tests.test_sandbox_personas import _switch

pytestmark = pytest.mark.integration
village = test_sandbox_http.village
_csrf = test_sandbox_http._csrf


def _native_factory(village, prefix):
    """Open independent native connections to this test's private workspace."""
    manager = village.extensions["villages"]
    workspace = manager.workspaces[prefix.rsplit("/", 1)[1]]
    native_dsn = make_dsn(manager.postgres.dsn, dbname=workspace["name"])

    @contextmanager
    def connect():
        connection = psycopg2.connect(native_dsn, cursor_factory=RealDictCursor)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    return connect


def _add_operator(client, prefix, cid):
    """Confirm another fictional participant through the ordinary controls."""
    page = client.get(prefix + "/leg/dashboard?cid=" + cid)
    assert (
        client.post(
            prefix + f"/leg/community/{cid}/invite",
            data={
                "csrf_token": _csrf(page),
                "invite_email": "sonnwil-operator@example.org",
            },
        ).status_code
        == 302
    )
    _switch(client, prefix, "operator")
    page = client.get(prefix + "/leg/dashboard?cid=" + cid)
    assert (
        client.post(
            prefix + f"/leg/community/{cid}/confirm", data={"csrf_token": _csrf(page)}
        ).status_code
        == 302
    )
    _switch(client, prefix, "founder")


def _documents(village, client, cid):
    """Read all retained bundle evidence, including superseded documents."""
    with (
        _workspace_connection(village, client) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT id, signing_status, pdf_data, participant_manifest FROM leg_documents WHERE community_id=%s ORDER BY id",
            (cid,),
        )
        return [(row[0], row[1], bytes(row[2]), row[3]) for row in cursor.fetchall()]


@pytest.mark.parametrize("state", ["acknowledged", "rejected"])
def test_stale_delivered_form_after_response_returns_conflict(village, state):
    client = village.test_client()
    prefix, cid = _signed(client)
    guide = prefix + "/sandbox/formation?cid=" + cid
    path = prefix + f"/sandbox/formation/{cid}/vnb"
    fields = {"csrf_token": _csrf(client.get(guide)), "state": state}
    assert client.post(path, data=fields).status_code == 302
    with (
        _workspace_connection(village, client) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT case_id FROM vnb_submission_cases WHERE community_id=%s", (cid,)
        )
        case = cursor.fetchone()[0]
    assert (
        client.post(
            path, data={**fields, "state": "delivered", "case_id": case}
        ).status_code
        == 409
    )
    with (
        _workspace_connection(village, client) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT state FROM vnb_submission_cases WHERE community_id=%s", (cid,)
        )
        assert cursor.fetchall() == [(state,)]


def test_completed_local_bundle_can_be_replaced_only_after_manifest_changes(village):
    client = village.test_client()
    prefix, cid = _signed(client)
    guide = prefix + "/sandbox/formation?cid=" + cid
    path = prefix + f"/leg/community/{cid}/documents"
    old = _documents(village, client, cid)
    old_fingerprint = re.search(
        r'name="bundle" value="([a-f0-9]{64})"', client.get(guide).text
    ).group(1)
    assert (
        client.post(path, data={"csrf_token": _csrf(client.get(guide))}).status_code
        == 302
    )
    assert _documents(village, client, cid) == old

    _add_operator(client, prefix, cid)
    assert 'name="bundle"' not in client.get(guide).text
    assert (
        client.post(path, data={"csrf_token": _csrf(client.get(guide))}).status_code
        == 302
    )
    page = client.get(guide)
    assert 'name="bundle"' in page.text
    fingerprint = re.search(r'name="bundle" value="([a-f0-9]{64})"', page.text).group(1)
    assert fingerprint != old_fingerprint
    documents = _documents(village, client, cid)
    assert documents[: len(old)] == [
        (row[0], "superseded", row[2], row[3]) for row in old
    ]
    assert len(documents[len(old) :]) == 5
    assert all(
        row[1] == "unsigned" and len(row[3]) == 4 for row in documents[len(old) :]
    )
    signatures = prefix + f"/sandbox/formation/{cid}/signatures"
    assert (
        client.post(
            signatures,
            data={
                "csrf_token": _csrf(page),
                "bundle": old_fingerprint,
                "confirm": "yes",
            },
        ).status_code
        == 409
    )
    for role in ("founder", "resident", "producer", "operator"):
        _switch(client, prefix, role)
        page = client.get(guide)
        assert (
            client.post(
                signatures,
                data={
                    "csrf_token": _csrf(page),
                    "bundle": fingerprint,
                    "confirm": "yes",
                },
            ).status_code
            == 302
        )
    with (
        _workspace_connection(village, client) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT status FROM communities WHERE community_id=%s", (cid,))
        assert cursor.fetchone()[0] == "signatures_pending"
        cursor.execute(
            "SELECT count(*) FROM leg_signature_receipts WHERE community_id=%s", (cid,)
        )
        assert cursor.fetchone()[0] == 7
    assert _documents(village, client, cid)[: len(old)] == documents[: len(old)]
    _switch(client, prefix, "founder")
    assert (
        client.post(
            prefix + f"/sandbox/formation/{cid}/vnb",
            data={"csrf_token": _csrf(client.get(guide)), "state": "acknowledged"},
        ).status_code
        == 302
    )


def test_submission_cannot_deliver_a_bundle_replaced_after_its_initial_read(
    village, monkeypatch
):
    """Pause a real submission across regeneration and fresh signatures."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    import database
    import sandbox_formation
    import vnb_exchange
    from store import vnb_exchange as repository

    client = village.test_client()
    prefix, cid = _signed(client)
    monkeypatch.setattr(database, "get_connection", _native_factory(village, prefix))
    entered, resume = Event(), Event()

    class WaitingCases:
        def claim(self, submission):
            entered.set()
            assert resume.wait(90), "the replacement must release the paused claim"
            return repository.claim(submission)

        def record_outcome(self, case_id, outcome):
            return repository.record_outcome(case_id, outcome)

    class CountingAdapter(sandbox_formation.SimulationAdapter):
        calls = 0

        def submit(self, package, *, idempotency_key):
            self.calls += 1
            return super().submit(package, idempotency_key=idempotency_key)

    adapter = CountingAdapter()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            vnb_exchange.submit_formation,
            vnb_exchange.FormationSubmission(cid, "sonnwil-founder"),
            adapter,
            cases=WaitingCases(),
        )
        try:
            assert entered.wait(15), "the original signed bundle must reach its claim"
            _add_operator(client, prefix, cid)
            guide = prefix + "/sandbox/formation?cid=" + cid
            assert (
                client.post(
                    prefix + f"/leg/community/{cid}/documents",
                    data={"csrf_token": _csrf(client.get(guide))},
                ).status_code
                == 302
            )
            for role in ("founder", "resident", "producer", "operator"):
                _switch(client, prefix, role)
                page = client.get(guide)
                fingerprint = re.search(
                    r'name="bundle" value="([a-f0-9]{64})"', page.text
                ).group(1)
                assert (
                    client.post(
                        prefix + f"/sandbox/formation/{cid}/signatures",
                        data={
                            "csrf_token": _csrf(page),
                            "bundle": fingerprint,
                            "confirm": "yes",
                        },
                    ).status_code
                    == 302
                )
        finally:
            resume.set()
        with pytest.raises(repository.VnbSubmissionConflict):
            pending.result(timeout=15)
    assert adapter.calls == 0
    with (
        _workspace_connection(village, client) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT count(*) FROM vnb_submission_cases WHERE community_id=%s", (cid,)
        )
        assert cursor.fetchone()[0] == 0


@pytest.mark.parametrize(
    "protection", ["provider", "signed_url", "legacy", "submission"]
)
def test_regeneration_preserves_provider_legacy_and_submitted_evidence(
    village, protection
):
    client = village.test_client()
    prefix, cid = _signed(client)
    if protection == "submission":
        guide = prefix + "/sandbox/formation?cid=" + cid
        assert (
            client.post(
                prefix + f"/sandbox/formation/{cid}/vnb",
                data={"csrf_token": _csrf(client.get(guide)), "state": "acknowledged"},
            ).status_code
            == 302
        )
    else:
        with (
            _workspace_connection(village, client) as connection,
            connection.cursor() as cursor,
        ):
            if protection == "provider":
                cursor.execute(
                    "UPDATE leg_documents SET deepsign_document_id='external-proof' WHERE community_id=%s",
                    (cid,),
                )
            elif protection == "signed_url":
                cursor.execute(
                    "UPDATE leg_documents SET signed_pdf_url='https://example.invalid/signed.pdf' WHERE community_id=%s",
                    (cid,),
                )
            else:
                cursor.execute(
                    "DELETE FROM leg_signature_receipts WHERE community_id=%s", (cid,)
                )
    old = _documents(village, client, cid)
    _add_operator(client, prefix, cid)
    page = client.get(prefix + "/leg/dashboard?cid=" + cid)
    assert (
        client.post(
            prefix + f"/leg/community/{cid}/documents", data={"csrf_token": _csrf(page)}
        ).status_code
        == 302
    )
    assert _documents(village, client, cid) == old
