# SPDX-License-Identifier: AGPL-3.0-or-later
"""Retry a failed Netzbetreiber submission from the Gründung page (#959)."""

import re
import threading

import pytest

import vnb_exchange
from tests import test_community_create_postgres
from tests.test_dashboard_access_routes import _set_session

interest_database = test_community_create_postgres.interest_database
founder = test_community_create_postgres.founder
pytestmark = pytest.mark.integration

ADAPTER_KEY = "synthetic-vnb"
OUTAGE = TimeoutError("synthetic transport outage")
DELIVERED = vnb_exchange.DeliveryResult(
    state="delivered", request_id="synthetic-request", response_status="received"
)


class ScriptedAdapter:
    """Automated synthetic transport that returns one scripted result per call."""

    def __init__(self, *results, hold_call=None):
        self.results = list(results)
        self.calls = 0
        self.hold_call = hold_call
        self.entered = threading.Event()
        self.release = threading.Event()

    def contract(self):
        return vnb_exchange.ExchangeContract(
            adapter_key=ADAPTER_KEY,
            contract_version=vnb_exchange.SUPPORTED_CONTRACT_VERSION,
            capabilities=frozenset({vnb_exchange.FORMATION_SUBMISSION}),
        )

    def submit(self, package, *, idempotency_key):
        self.calls += 1
        result = self.results.pop(0)
        if self.calls == self.hold_call:
            self.entered.set()
            assert self.release.wait(30)
        if isinstance(result, Exception):
            raise result
        return result


def _cases(db, cid):
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT case_id, state, retryable, next_action
               FROM vnb_submission_cases WHERE community_id = %s
               ORDER BY created_at, case_id""",
            (cid,),
        )
        return [dict(row) for row in cur.fetchall()]


def _retry_path(cid, case_id):
    return f"/leg/community/{cid}/vnb-submissions/{case_id}/retry"


def _retry_form(page, cid, case_id):
    match = re.search(
        rf'<form method="post" action="{re.escape(_retry_path(cid, case_id))}"'
        r"[^>]*>(.*?)</form>",
        page,
        re.DOTALL,
    )
    return match.group(1) if match else None


@pytest.fixture
def signed(founder, monkeypatch):
    """A signed LEG whose administrator submits through an automated adapter."""
    client, db = founder
    _set_session(client, "founder")
    cid = db.create_community_record("Synthetic LEG", "founder", "simple", "")
    document_id = db.store_leg_document(
        cid, "dso_notification", b"%PDF synthetic signed", "anmeldung.pdf"
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE communities SET status = 'signatures_pending',
                      vnb_adapter_key = %s WHERE community_id = %s""",
            (ADAPTER_KEY, cid),
        )
        cur.execute(
            "UPDATE leg_documents SET signing_status = 'completed' WHERE id = %s",
            (document_id,),
        )
    assert db.save_building(
        "member",
        "member@example.invalid",
        {"address": "Synthetic 2", "lat": 47.2, "lon": 8.2},
        {},
        verified=True,
    )
    assert db.insert_invited_member(cid, "member", "founder")
    assert db.confirm_invited_member(cid, "member")
    with db.get_connection() as conn, conn.cursor() as cur:
        # The submission reads members with discovery consent; both are visible,
        # so a refusal comes from the role, not from visibility.
        cur.execute(
            """UPDATE consents SET share_with_neighbors = TRUE
               WHERE building_id IN ('founder', 'member')"""
        )

    def failed_with(adapter):
        monkeypatch.setitem(vnb_exchange.ADAPTERS, ADAPTER_KEY, adapter)
        response = client.post(
            f"/leg/community/{cid}/vnb-submissions", data={"csrf_token": "csrf-secret"}
        )
        assert response.status_code == 302
        [case] = _cases(db, cid)
        assert case["state"] == "failed"
        return case["case_id"]

    return client, db, cid, failed_with


def test_administrator_retries_a_failed_submission_as_a_new_attempt(signed):
    client, db, cid, failed_with = signed
    adapter = ScriptedAdapter(OUTAGE, DELIVERED)
    failed = failed_with(adapter)
    overview = f"/leg/dashboard?cid={cid}"

    form = _retry_form(client.get(overview).text, cid, failed)
    assert form is not None
    assert 'name="csrf_token" value="csrf-secret"' in form
    button = re.search(r"<button ([^>]*)>\s*Erneut einleiten\s*</button>", form)
    assert button and 'type="submit"' in button.group(1)
    assert "min-h-12" in button.group(1)

    response = client.post(_retry_path(cid, failed), data={"csrf_token": "csrf-secret"})

    assert response.status_code == 302
    assert response.location == overview
    first, retry = _cases(db, cid)
    assert first == {
        "case_id": failed,
        "state": "failed",
        "retryable": True,
        "next_action": "retry",
    }
    assert retry["case_id"] != failed
    assert retry["state"] == "delivered"
    assert adapter.calls == 2
    assert db.fetch_community_with_members(cid, "founder")["status"] == "dso_submitted"
    page = client.get(overview).text
    assert "Fehlgeschlagen" in page
    assert "Zugestellt" in page
    assert "Einleitung erneut möglich" not in page
    assert "/retry" not in page


def test_second_tab_cannot_retry_an_attempt_that_was_already_retried(signed):
    client, db, cid, failed_with = signed
    adapter = ScriptedAdapter(OUTAGE, OUTAGE)
    failed = failed_with(adapter)
    path = _retry_path(cid, failed)

    assert client.post(path, data={"csrf_token": "csrf-secret"}).status_code == 302
    attempts = _cases(db, cid)
    assert [case["state"] for case in attempts] == ["failed", "failed"]

    stale = client.post(path, data={"csrf_token": "csrf-secret"})

    assert stale.status_code == 302
    assert _cases(db, cid) == attempts
    assert adapter.calls == 2
    page = client.get(f"/leg/dashboard?cid={cid}").text
    assert _retry_form(page, cid, failed) is None
    assert _retry_form(page, cid, attempts[1]["case_id"]) is not None
    assert page.count("Einleitung erneut möglich") == 1


def test_concurrent_retry_while_delivery_is_in_flight_creates_one_attempt(signed):
    client, db, cid, failed_with = signed
    adapter = ScriptedAdapter(OUTAGE, DELIVERED, DELIVERED, hold_call=2)
    failed = failed_with(adapter)
    path = _retry_path(cid, failed)
    other_tab = client.application.test_client()
    _set_session(other_tab, "founder")
    responses = {}

    def first_tab():
        responses["first"] = client.post(path, data={"csrf_token": "csrf-secret"})

    worker = threading.Thread(target=first_tab)
    worker.start()
    try:
        assert adapter.entered.wait(30)
        responses["second"] = other_tab.post(path, data={"csrf_token": "csrf-secret"})
    finally:
        adapter.release.set()
        worker.join(30)

    assert responses["first"].status_code == 302
    assert responses["second"].status_code == 302
    assert [case["state"] for case in _cases(db, cid)] == ["failed", "delivered"]
    assert adapter.calls == 2


def test_member_without_the_role_gets_403_and_no_retry_offer(signed):
    client, db, cid, failed_with = signed
    adapter = ScriptedAdapter(OUTAGE, DELIVERED)
    failed = failed_with(adapter)
    before = _cases(db, cid)
    _set_session(client, "member")

    page = client.get(f"/leg/dashboard?cid={cid}").text
    response = client.post(_retry_path(cid, failed), data={"csrf_token": "csrf-secret"})

    assert "Fehlgeschlagen" in page
    assert "Einleitung erneut möglich" not in page
    assert "/retry" not in page
    assert response.status_code == 403
    assert _cases(db, cid) == before
    assert adapter.calls == 1


def test_retry_requires_the_session_csrf_token(signed):
    client, db, cid, failed_with = signed
    adapter = ScriptedAdapter(OUTAGE, DELIVERED)
    failed = failed_with(adapter)
    before = _cases(db, cid)

    for data in ({}, {"csrf_token": "forged"}):
        response = client.post(_retry_path(cid, failed), data=data)
        assert response.status_code == 400

    assert _cases(db, cid) == before
    assert adapter.calls == 1


def test_non_retryable_failure_offers_and_accepts_no_retry(signed):
    client, db, cid, failed_with = signed
    adapter = ScriptedAdapter(
        vnb_exchange.DeliveryResult(
            state="failed", retryable=False, failure_code="synthetic_refusal"
        ),
        DELIVERED,
    )
    failed = failed_with(adapter)
    before = _cases(db, cid)

    page = client.get(f"/leg/dashboard?cid={cid}").text
    response = client.post(_retry_path(cid, failed), data={"csrf_token": "csrf-secret"})

    assert "Netzbetreiber kontaktieren" in page
    assert "Einleitung erneut möglich" not in page
    assert "/retry" not in page
    assert response.status_code == 409
    assert _cases(db, cid) == before
    assert adapter.calls == 1


def test_existing_database_migrates_to_keep_failed_attempts(signed):
    client, db, cid, failed_with = signed
    with db.get_connection() as conn, conn.cursor() as cur:
        # Before #959 the table kept one case per payload in every state.
        cur.execute("DROP INDEX uq_vnb_submission_payload")
        cur.execute(
            """ALTER TABLE vnb_submission_cases ADD CONSTRAINT
               vnb_submission_cases_community_id_contract_version_payload__key
               UNIQUE (community_id, contract_version, payload_fingerprint)"""
        )
    db.create_tables()
    adapter = ScriptedAdapter(OUTAGE, DELIVERED)
    failed = failed_with(adapter)

    response = client.post(_retry_path(cid, failed), data={"csrf_token": "csrf-secret"})

    assert response.status_code == 302
    assert [case["state"] for case in _cases(db, cid)] == ["failed", "delivered"]
