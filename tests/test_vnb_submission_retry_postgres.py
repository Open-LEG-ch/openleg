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
    assert db.save_building(
        "manager",
        "manager@example.invalid",
        {"address": "Synthetic 3", "lat": 47.2, "lon": 8.2},
        {},
        verified=True,
    )
    assert db.insert_invited_member(cid, "manager", "founder")
    assert db.confirm_invited_member(cid, "manager")
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE community_members SET access_roles = '["documents"]'
               WHERE community_id = %s AND building_id = 'manager'""",
            (cid,),
        )
        # The submission reads members with discovery consent; all are visible,
        # so a refusal comes from the role, not from visibility.
        cur.execute(
            """UPDATE consents SET share_with_neighbors = TRUE
               WHERE building_id IN ('founder', 'member', 'manager')"""
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
    adapter = ScriptedAdapter(OUTAGE, DELIVERED)
    failed = failed_with(adapter)
    with db.get_connection() as conn, conn.cursor() as cur:
        # Before #959 the table kept one case per payload in every state,
        # under the constraint name PostgreSQL generates.
        cur.execute("DROP INDEX uq_vnb_submission_payload")
        cur.execute(
            """ALTER TABLE vnb_submission_cases
               ADD UNIQUE (community_id, contract_version, payload_fingerprint)"""
        )
    db.create_tables()

    response = client.post(_retry_path(cid, failed), data={"csrf_token": "csrf-secret"})

    assert response.status_code == 302
    assert [case["state"] for case in _cases(db, cid)] == ["failed", "delivered"]


def test_attempts_are_ordered_by_claim_not_by_transaction_start(signed):
    from store import vnb_exchange as cases

    client, db, cid, failed_with = signed
    adapter = ScriptedAdapter(OUTAGE, OUTAGE)
    failed = failed_with(adapter)
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT contract_version, payload_fingerprint
               FROM vnb_submission_cases WHERE case_id = %s""",
            (failed,),
        )
        identity = cur.fetchone()
    with db.get_connection() as confirmation:
        # An agent confirmation opens its transaction before it waits for the
        # community lock; a person's retry can commit in between.
        confirmation.cursor().execute("SELECT 1")
        retry = client.post(
            _retry_path(cid, failed), data={"csrf_token": "csrf-secret"}
        )
        assert retry.status_code == 302
        with cases.confirmation_transaction(confirmation):
            claimed = cases.claim(
                vnb_exchange.SubmissionClaim(
                    community_id=cid,
                    actor_building_id="founder",
                    adapter_key=ADAPTER_KEY,
                    contract_version=identity["contract_version"],
                    capability_snapshot=(),
                    payload_fingerprint=identity["payload_fingerprint"],
                )
            )

    newest_first = [case["case_id"] for case in db.list_vnb_submission_cases(cid)]
    assert newest_first[0] == claimed["case_id"]
    assert newest_first[-1] == failed
    page = client.get(f"/leg/dashboard?cid={cid}").text
    assert "/retry" not in page


def _claim(db, cid, failed, actor, retry_of=None):
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT contract_version, payload_fingerprint
               FROM vnb_submission_cases WHERE case_id = %s""",
            (failed,),
        )
        identity = cur.fetchone()
        cur.execute(
            "SELECT id FROM leg_documents WHERE community_id = %s ORDER BY id",
            (cid,),
        )
        documents = tuple(row["id"] for row in cur.fetchall())
    return vnb_exchange.SubmissionClaim(
        community_id=cid,
        actor_building_id=actor,
        adapter_key=ADAPTER_KEY,
        contract_version=identity["contract_version"],
        capability_snapshot=(),
        payload_fingerprint=identity["payload_fingerprint"],
        document_ids=documents,
        retry_of=retry_of,
    )


@pytest.mark.parametrize("named", [True, False])
def test_new_attempt_rechecks_the_role_under_the_community_lock(signed, named):
    from store import vnb_exchange as cases

    _client, db, cid, failed_with = signed
    failed = failed_with(ScriptedAdapter(OUTAGE))
    before = _cases(db, cid)
    # The domain read the role before an administrator removed it; the claim
    # must refuse a member who lacks the documents role.
    claim = _claim(db, cid, failed, "member", failed if named else None)

    with pytest.raises(db.VnbSubmissionConflict):
        cases.claim(claim)

    assert _cases(db, cid) == before


def test_stale_retry_stays_single_after_the_clock_steps_back(signed):
    client, db, cid, failed_with = signed
    adapter = ScriptedAdapter(OUTAGE, OUTAGE)
    failed = failed_with(adapter)
    with db.get_connection() as conn, conn.cursor() as cur:
        # The failure was stamped before the server clock stepped back.
        cur.execute(
            """UPDATE vnb_submission_cases
               SET created_at = clock_timestamp() + interval '1 hour'
               WHERE case_id = %s""",
            (failed,),
        )
    path = _retry_path(cid, failed)

    assert client.post(path, data={"csrf_token": "csrf-secret"}).status_code == 302
    attempts = _cases(db, cid)
    stale = client.post(path, data={"csrf_token": "csrf-secret"})

    assert attempts[0]["case_id"] == failed
    assert stale.status_code == 302
    assert _cases(db, cid) == attempts
    assert adapter.calls == 2
    page = client.get(f"/leg/dashboard?cid={cid}").text
    assert _retry_form(page, cid, failed) is None
    assert _retry_form(page, cid, attempts[1]["case_id"]) is not None


def test_new_attempt_rechecks_the_bundle_under_the_community_lock(signed):
    from store import vnb_exchange as cases

    _client, db, cid, failed_with = signed
    failed = failed_with(ScriptedAdapter(OUTAGE))
    claim = _claim(db, cid, failed, "founder", failed)
    document_id = db.store_leg_document(
        cid, "teilnehmervertrag", b"%PDF synthetic later", "later.pdf"
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        # The bundle changed after the retry read it and before it claimed.
        cur.execute(
            "UPDATE leg_documents SET signing_status = 'completed' WHERE id = %s",
            (document_id,),
        )
    before = _cases(db, cid)

    with pytest.raises(db.VnbSubmissionConflict):
        cases.claim(claim)

    assert _cases(db, cid) == before


def test_document_manager_is_offered_the_retry_and_can_use_it(signed):
    client, db, cid, failed_with = signed
    adapter = ScriptedAdapter(OUTAGE, DELIVERED)
    failed = failed_with(adapter)
    _set_session(client, "manager")

    form = _retry_form(client.get(f"/leg/dashboard?cid={cid}").text, cid, failed)
    response = client.post(_retry_path(cid, failed), data={"csrf_token": "csrf-secret"})

    assert form is not None
    assert response.status_code == 302
    assert [case["state"] for case in _cases(db, cid)] == ["failed", "delivered"]


def test_retry_is_not_offered_when_the_post_would_refuse_it(signed):
    client, db, cid, failed_with = signed
    adapter = ScriptedAdapter(OUTAGE, DELIVERED)
    failed = failed_with(adapter)
    with db.get_connection() as conn, conn.cursor() as cur:
        # The administrator stops sharing with neighbours; the submission's
        # member read then no longer lists them.
        cur.execute(
            """UPDATE consents SET share_with_neighbors = FALSE
               WHERE building_id = 'founder'"""
        )

    page = client.get(f"/leg/dashboard?cid={cid}").text
    response = client.post(_retry_path(cid, failed), data={"csrf_token": "csrf-secret"})

    assert "Fehlgeschlagen" in page
    assert "Einleitung erneut möglich" not in page
    assert "/retry" not in page
    assert response.status_code == 403
    assert adapter.calls == 1
