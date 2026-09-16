# SPDX-License-Identifier: AGPL-3.0-or-later
"""Persistence interface for idempotent VNB submission cases."""

from contextlib import contextmanager

import pytest

import database
import vnb_exchange
from store import vnb_exchange as store
from store.operator_api import event_id_for


class Cursor:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.executed = []
        self.rowcount = 1

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchone(self):
        return next(self.rows)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def connection(cursor):
    @contextmanager
    def factory():
        yield Connection(cursor)

    return factory


def test_mutation_handover_excludes_missing_package_bytes(monkeypatch):
    cursor = Cursor([None])
    monkeypatch.setattr(database, "get_connection", connection(cursor))

    assert store.get_mutation_manual_package("community-1", "case-1") is None

    query, params = cursor.executed[0]
    assert "manual_package IS NOT NULL" in query
    assert params == ("community-1", "case-1")


def test_claim_uses_the_stable_submission_identity(monkeypatch):
    row = {
        "case_id": "case-1",
        "community_id": "community-1",
        "state": "claimed",
        "contract_version": "vnb-formation/1",
        "payload_fingerprint": "f" * 64,
    }
    cursor = Cursor([row])
    monkeypatch.setattr(database, "get_connection", connection(cursor))
    claim = vnb_exchange.SubmissionClaim(
        community_id="community-1",
        actor_building_id="actor-1",
        adapter_key="manual",
        contract_version="vnb-formation/1",
        capability_snapshot=(),
        payload_fingerprint="f" * 64,
    )

    assert store.claim(claim) == row

    query, params = cursor.executed[0]
    assert "ON CONFLICT (community_id, contract_version, payload_fingerprint)" in query
    assert params[1] == "community-1"
    assert params[2] == "actor-1"
    assert params[3:6] == ("manual", "vnb-formation/1", "f" * 64)


def test_record_outcome_updates_only_a_claimed_case(monkeypatch):
    row = {
        "case_id": "case-1",
        "community_id": "community-1",
        "state": "prepared",
        "contract_version": "vnb-formation/1",
        "payload_fingerprint": "f" * 64,
        "next_action": "download_and_deliver",
        "manual_package": b"zip",
    }
    cursor = Cursor([row])
    monkeypatch.setattr(database, "get_connection", connection(cursor))
    outcome = vnb_exchange.SubmissionOutcome(
        case_id="case-1",
        state="prepared",
        contract_version="vnb-formation/1",
        payload_fingerprint="f" * 64,
        next_action="download_and_deliver",
        manual_package=b"zip",
    )

    stored = store.record_outcome("case-1", outcome)
    assert {key: stored[key] for key in row} == row
    assert stored["event_id"] == event_id_for("formation.submission.prepared", "case-1")

    query, params = cursor.executed[0]
    assert "WHERE case_id = %s AND state = 'claimed'" in query
    assert params[-1] == "case-1"
    assert "INSERT INTO operator_events" in cursor.executed[1][0]
    assert "INSERT INTO operator_webhook_deliveries" in cursor.executed[2][0]


def test_claim_mutation_checks_stable_identity_and_pending_participant(monkeypatch):
    row = {
        "case_id": "mutation-case-1",
        "mutation_id": "mutation-1",
        "community_id": "community-1",
        "state": "claimed",
        "payload_fingerprint": "f" * 64,
    }
    cursor = Cursor([None, None, row])
    monkeypatch.setattr(database, "get_connection", connection(cursor))
    claim = vnb_exchange.MutationClaim(
        mutation_id="mutation-1",
        community_id="community-1",
        participant_id="building-2",
        actor_building_id="actor-1",
        mutation_type="join",
        effective_date="2026-10-01",
        source_agreement_id="agreement-v3",
        before={"status": "invited"},
        after={"status": "confirmed"},
        adapter_key="manual-handover",
        contract_version="vnb-membership/1",
        capability_snapshot=(),
        payload_fingerprint="f" * 64,
    )
    assert store.claim_mutation(claim) == row
    assert "mutation_id = %s" in cursor.executed[0][0]
    assert "state IN ('claimed', 'prepared', 'delivered')" in cursor.executed[1][0]
    assert "INSERT INTO vnb_mutation_cases" in cursor.executed[2][0]


def test_mutation_outcome_appends_evidence_before_projection(monkeypatch):
    row = {
        "case_id": "mutation-case-1",
        "mutation_id": "mutation-1",
        "community_id": "community-1",
        "state": "rejected",
        "contract_version": "vnb-membership/1",
        "payload_fingerprint": "f" * 64,
        "next_action": "review_rejection",
    }
    cursor = Cursor([row])
    monkeypatch.setattr(database, "get_connection", connection(cursor))
    outcome = vnb_exchange.MutationOutcome(
        case_id="mutation-case-1",
        mutation_id="mutation-1",
        state="rejected",
        contract_version="vnb-membership/1",
        payload_fingerprint="f" * 64,
        next_action="review_rejection",
        request_id="request-9",
        response_status="bad-date",
        evidence=b"reason",
    )
    stored = store.record_mutation_outcome("mutation-case-1", outcome)
    assert {key: stored[key] for key in row} == row
    assert stored["event_id"] == event_id_for(
        "membership.mutation.rejected", "mutation-case-1"
    )
    assert "INSERT INTO vnb_mutation_events" in cursor.executed[0][0]
    assert "ON CONFLICT" in cursor.executed[0][0]
    assert "UPDATE vnb_mutation_cases" in cursor.executed[1][0]
    assert "INSERT INTO operator_events" in cursor.executed[2][0]
    assert "INSERT INTO operator_webhook_deliveries" in cursor.executed[3][0]


def test_acknowledged_outcome_advances_only_a_signatures_pending_community(
    monkeypatch,
):
    row = {
        "case_id": "case-1",
        "community_id": "community-1",
        "state": "acknowledged",
        "contract_version": "vnb-formation/1",
        "payload_fingerprint": "f" * 64,
        "next_action": "none",
    }
    cursor = Cursor([row])
    monkeypatch.setattr(database, "get_connection", connection(cursor))
    outcome = vnb_exchange.SubmissionOutcome(
        case_id="case-1",
        state="acknowledged",
        contract_version="vnb-formation/1",
        payload_fingerprint="f" * 64,
        next_action="none",
    )

    store.record_outcome("case-1", outcome)

    transition_sql, transition_params = cursor.executed[0]
    assert "status = 'dso_submitted'" in transition_sql
    assert "status = 'signatures_pending'" in transition_sql
    assert transition_params == ("case-1",)


def _mutation_claim(payload_fingerprint="f" * 64):
    return vnb_exchange.MutationClaim(
        mutation_id="mutation-1",
        community_id="community-1",
        participant_id="building-2",
        actor_building_id="actor-1",
        mutation_type="join",
        effective_date="2026-10-01",
        source_agreement_id="agreement-v3",
        before={"status": "invited"},
        after={"status": "confirmed"},
        adapter_key="manual-handover",
        contract_version="vnb-membership/1",
        capability_snapshot=(),
        payload_fingerprint=payload_fingerprint,
    )


def _acknowledged_row():
    return {
        "case_id": "mutation-case-1",
        "mutation_id": "mutation-1",
        "community_id": "community-1",
        "state": "acknowledged",
        "contract_version": "vnb-membership/1",
        "payload_fingerprint": "f" * 64,
        "next_action": "none",
    }


def test_duplicate_acknowledgement_enqueues_the_event_only_once(monkeypatch):
    row = _acknowledged_row()
    cursor = Cursor([{"id": 1}, row, None, row])
    monkeypatch.setattr(database, "get_connection", connection(cursor))

    first = store.record_mutation_response(
        "community-1", "mutation-case-1", "acknowledged",
        "request-1", "accepted", b"ack",
    )
    second = store.record_mutation_response(
        "community-1", "mutation-case-1", "acknowledged",
        "request-1", "accepted", b"ack",
    )

    assert first["event_id"] == event_id_for(
        "membership.mutation.acknowledged", "mutation-case-1"
    )
    assert "event_id" not in second
    assert len(cursor.executed) == 6
    assert "ON CONFLICT (case_id, state, external_request_id, response_status)" in (
        cursor.executed[0][0]
    )
    assert "INSERT INTO operator_events" in cursor.executed[2][0]
    assert "INSERT INTO operator_webhook_deliveries" in cursor.executed[3][0]
    assert "INSERT INTO vnb_mutation_events" in cursor.executed[4][0]
    assert "UPDATE vnb_mutation_cases" in cursor.executed[5][0]


def test_late_response_to_finalized_case_keeps_the_stored_projection(monkeypatch):
    row = _acknowledged_row()
    cursor = Cursor([None, None, row])
    monkeypatch.setattr(database, "get_connection", connection(cursor))

    stored = store.record_mutation_response(
        "community-1", "mutation-case-1", "acknowledged",
        "request-1", "accepted", b"ack",
    )

    assert stored == row
    assert len(cursor.executed) == 3
    assert "state NOT IN ('acknowledged', 'rejected', 'superseded')" in (
        cursor.executed[1][0]
    )
    assert "SELECT * FROM vnb_mutation_cases" in cursor.executed[2][0]


def test_response_for_unknown_case_raises(monkeypatch):
    cursor = Cursor([None, None, None])
    monkeypatch.setattr(database, "get_connection", connection(cursor))

    with pytest.raises(store.VnbExchangeStoreError, match="was not found"):
        store.record_mutation_response(
            "community-1", "missing-case", "acknowledged",
            "request-1", "accepted", b"ack",
        )


def test_claim_mutation_rejects_reused_id_with_changed_content(monkeypatch):
    cursor = Cursor([{"payload_fingerprint": "a" * 64}])
    monkeypatch.setattr(database, "get_connection", connection(cursor))

    with pytest.raises(store.VnbSubmissionConflict) as raised:
        store.claim_mutation(_mutation_claim(payload_fingerprint="f" * 64))

    assert "anderen Inhalt" in str(raised.value)


def test_claim_mutation_rejects_second_open_mutation_for_a_participant(monkeypatch):
    cursor = Cursor([None, {"mutation_id": "mutation-0"}])
    monkeypatch.setattr(database, "get_connection", connection(cursor))

    with pytest.raises(store.VnbSubmissionConflict) as raised:
        store.claim_mutation(_mutation_claim())

    assert "bereits eine Mutation offen" in str(raised.value)
    assert "participant_id = %s" in cursor.executed[1][0]
