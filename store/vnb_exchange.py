# SPDX-License-Identifier: AGPL-3.0-or-later
"""Persistence for versioned VNB submission cases."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from psycopg2.errors import UniqueViolation
from psycopg2.extras import Json

from store.operator_api import enqueue_event

if TYPE_CHECKING:
    from vnb_exchange import (
        MutationClaim,
        MutationOutcome,
        SubmissionClaim,
        SubmissionOutcome,
    )


class VnbExchangeStoreError(RuntimeError):
    """A VNB submission case could not be persisted."""


class VnbSubmissionConflict(VnbExchangeStoreError):
    """Another active submission prevents this formation attempt."""


def claim_mutation(submission: MutationClaim) -> dict:
    """Claim a stable mutation, rejecting re-use and participant conflicts."""
    case_id = str(uuid.uuid4())
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT * FROM vnb_mutation_cases
               WHERE community_id = %s AND mutation_id = %s FOR UPDATE""",
            (submission.community_id, submission.mutation_id),
        )
        existing = cur.fetchone()
        if existing:
            existing = dict(existing)
            if existing["payload_fingerprint"] != submission.payload_fingerprint:
                raise VnbSubmissionConflict(
                    "Diese Mutations-ID bezeichnet bereits einen anderen Inhalt."
                )
            return existing
        cur.execute(
            """SELECT mutation_id FROM vnb_mutation_cases
               WHERE community_id = %s AND participant_id = %s
                 AND state IN ('claimed', 'prepared', 'delivered')
               LIMIT 1 FOR UPDATE""",
            (submission.community_id, submission.participant_id),
        )
        conflict = cur.fetchone()
        if conflict:
            raise VnbSubmissionConflict(
                "Für diese teilnehmende Person ist bereits eine Mutation offen. "
                "Schliessen oder ersetzen Sie diese zuerst."
            )
        try:
            cur.execute(
                """INSERT INTO vnb_mutation_cases (
                   case_id, mutation_id, community_id, participant_id, created_by,
                   mutation_type, effective_date, source_agreement_id,
                   before_facts, after_facts, adapter_key, contract_version,
                   capability_snapshot, payload_fingerprint, state, next_action
               ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                         'claimed', 'deliver') RETURNING *""",
                (
                    case_id,
                    submission.mutation_id,
                    submission.community_id,
                    submission.participant_id,
                    submission.actor_building_id,
                    submission.mutation_type,
                    submission.effective_date,
                    submission.source_agreement_id,
                    Json(submission.before),
                    Json(submission.after),
                    submission.adapter_key,
                    submission.contract_version,
                    Json(list(submission.capability_snapshot)),
                    submission.payload_fingerprint,
                ),
            )
        except UniqueViolation as error:
            raise VnbSubmissionConflict(
                "Für diese Mutations-ID wird bereits ein Vorgang angelegt."
            ) from error
        return dict(cur.fetchone())


def record_mutation_outcome(case_id: str, outcome: MutationOutcome) -> dict:
    """Append evidence, then update only the VNB processing projection."""
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO vnb_mutation_events (
                   case_id, state, external_request_id, response_status,
                   evidence, retryable, failure_code)
               SELECT case_id, %s, %s, %s, %s, %s, %s
               FROM vnb_mutation_cases WHERE case_id = %s AND state = 'claimed'
               ON CONFLICT (case_id, state, external_request_id, response_status)
               DO NOTHING""",
            (
                outcome.state,
                outcome.request_id,
                outcome.response_status,
                outcome.evidence,
                outcome.retryable,
                outcome.failure_code,
                case_id,
            ),
        )
        cur.execute(
            """UPDATE vnb_mutation_cases SET state = %s, next_action = %s,
                   external_request_id = %s, response_status = %s,
                   manual_package = %s, retryable = %s, failure_code = %s,
                   updated_at = CURRENT_TIMESTAMP
               WHERE case_id = %s AND state = 'claimed' RETURNING *""",
            (
                outcome.state,
                outcome.next_action,
                outcome.request_id,
                outcome.response_status,
                outcome.manual_package,
                outcome.retryable,
                outcome.failure_code,
                case_id,
            ),
        )
        row = cur.fetchone()
        if not row:
            raise VnbExchangeStoreError("VNB mutation outcome was not recorded")
        row = dict(row)
        row["event_id"] = enqueue_event(
            cur,
            f"membership.mutation.{outcome.state}",
            case_id,
            row["community_id"],
            {
                "case_id": case_id,
                "mutation_id": row["mutation_id"],
                "state": outcome.state,
            },
        )
        return row


def list_mutations(community_id: str, limit: int = 100) -> list[dict]:
    """List the VNB projection with immutable LEG facts, scoped by community."""
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT case_id, mutation_id, community_id, participant_id,
                      mutation_type, effective_date, source_agreement_id,
                      before_facts, after_facts, state, next_action,
                      response_status, created_at, updated_at
               FROM vnb_mutation_cases WHERE community_id = %s
               ORDER BY created_at DESC LIMIT %s""",
            (community_id, max(1, min(int(limit), 100))),
        )
        return [dict(row) for row in cur.fetchall()]


def get_mutation_manual_package(community_id: str, case_id: str) -> dict | None:
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT case_id, community_id, manual_package
               FROM vnb_mutation_cases
               WHERE community_id = %s AND case_id = %s AND state = 'prepared'
                 AND manual_package IS NOT NULL""",
            (community_id, case_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def mark_mutation_manual_delivered(
    community_id: str, case_id: str, actor_building_id: str
) -> dict:
    """Append manual delivery evidence without modifying the LEG record."""
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE vnb_mutation_cases SET state = 'delivered',
                      next_action = 'await_acknowledgement', updated_at = CURRENT_TIMESTAMP
               WHERE community_id = %s AND case_id = %s AND state = 'prepared'
               RETURNING *""",
            (community_id, case_id),
        )
        row = cur.fetchone()
        if not row:
            raise VnbExchangeStoreError("Mutation ist nicht zur Übergabe bereit.")
        cur.execute(
            """INSERT INTO vnb_mutation_events (case_id, state, response_status)
               VALUES (%s, 'delivered', %s)
               ON CONFLICT (case_id, state, external_request_id, response_status)
               DO NOTHING""",
            (case_id, f"manual:{actor_building_id}"),
        )
        row = dict(row)
        row["event_id"] = enqueue_event(
            cur,
            "membership.mutation.delivered",
            case_id,
            community_id,
            {
                "case_id": case_id,
                "mutation_id": row["mutation_id"],
                "state": "delivered",
            },
        )
        return row


def record_mutation_response(
    community_id: str,
    case_id: str,
    state: str,
    request_id: str,
    response_status: str,
    evidence: bytes,
) -> dict:
    """Append an idempotent VNB response and advance only its projection."""
    if state not in {"acknowledged", "rejected", "failed", "superseded"}:
        raise VnbExchangeStoreError("Ungültiger VNB-Mutationsstatus.")
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT * FROM vnb_mutation_cases
               WHERE community_id = %s AND case_id = %s FOR UPDATE""",
            (community_id, case_id),
        )
        existing = cur.fetchone()
        if existing is None:
            raise VnbExchangeStoreError("VNB mutation case was not found")
        if existing["state"] in {"acknowledged", "rejected", "superseded"}:
            # A late or repeated response for a case already in a final
            # state keeps the stored projection and writes nothing.
            return dict(existing)
        cur.execute(
            """INSERT INTO vnb_mutation_events
                   (case_id, state, external_request_id, response_status, evidence)
               SELECT id, %s, %s, %s, %s FROM vnb_mutation_cases
               WHERE community_id = %s AND case_id = %s
               ON CONFLICT (case_id, state, external_request_id, response_status)
               DO NOTHING RETURNING id""",
            (state, request_id, response_status, evidence, community_id, case_id),
        )
        inserted = cur.fetchone()
        cur.execute(
            """UPDATE vnb_mutation_cases SET state = %s,
                      next_action = CASE WHEN %s = 'rejected' THEN 'review_rejection'
                                         WHEN %s = 'failed' THEN 'retry' ELSE 'none' END,
                      external_request_id = %s, response_status = %s,
                      updated_at = CURRENT_TIMESTAMP
               WHERE community_id = %s AND case_id = %s
               RETURNING *""",
            (state, state, state, request_id, response_status, community_id, case_id),
        )
        row = cur.fetchone()
        if not row:
            raise VnbExchangeStoreError("VNB mutation case was not found")
        # Duplicate acknowledgements return the same projection and no new event.
        row = dict(row)
        if inserted:
            row["event_id"] = enqueue_event(
                cur,
                f"membership.mutation.{state}",
                case_id,
                community_id,
                {
                    "case_id": case_id,
                    "mutation_id": row["mutation_id"],
                    "state": state,
                },
            )
        return row


def _get_connection():
    import database

    return database.get_connection()


def claim(submission: SubmissionClaim) -> dict:
    """Claim one stable formation submission or return its existing case."""
    case_id = str(uuid.uuid4())
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO vnb_submission_cases (
                    case_id, community_id, created_by, adapter_key,
                    contract_version, payload_fingerprint,
                    capability_snapshot, state, next_action
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'claimed', 'deliver')
                ON CONFLICT (community_id, contract_version, payload_fingerprint)
                DO UPDATE SET
                    state = CASE
                        WHEN vnb_submission_cases.state = 'failed'
                             AND vnb_submission_cases.retryable
                        THEN 'claimed'
                        ELSE vnb_submission_cases.state
                    END,
                    next_action = CASE
                        WHEN vnb_submission_cases.state = 'failed'
                             AND vnb_submission_cases.retryable
                        THEN 'deliver'
                        ELSE vnb_submission_cases.next_action
                    END,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING *
            """,
                (
                    case_id,
                    submission.community_id,
                    submission.actor_building_id,
                    submission.adapter_key,
                    submission.contract_version,
                    submission.payload_fingerprint,
                    Json(list(submission.capability_snapshot)),
                ),
            )
            row = cur.fetchone()
            if not row:
                raise VnbExchangeStoreError("VNB submission claim returned no case")
            return dict(row)
    except UniqueViolation as error:
        raise VnbSubmissionConflict(
            "Another VNB submission is already active for this community"
        ) from error


def record_outcome(case_id: str, outcome: SubmissionOutcome) -> dict:
    """Finalize a newly claimed delivery result without overwriting replays."""
    with _get_connection() as conn, conn.cursor() as cur:
        if outcome.state in {"delivered", "acknowledged"}:
            cur.execute(
                """
                    UPDATE communities
                    SET status = 'dso_submitted',
                        dso_submitted_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE community_id = (
                        SELECT community_id FROM vnb_submission_cases
                        WHERE case_id = %s AND state = 'claimed'
                    ) AND status = 'signatures_pending'
                """,
                (case_id,),
            )
            if cur.rowcount != 1:
                raise VnbExchangeStoreError(
                    "Formation state does not permit submission"
                )
        cur.execute(
            """
                UPDATE vnb_submission_cases
                SET state = %s,
                    next_action = %s,
                    external_request_id = %s,
                    response_status = %s,
                    manual_package = %s,
                    response_evidence = %s,
                    retryable = %s,
                    failure_code = %s,
                    attempted_at = CASE
                        WHEN %s <> 'prepared' THEN CURRENT_TIMESTAMP
                        ELSE attempted_at
                    END,
                    delivered_at = CASE
                        WHEN %s IN ('delivered', 'acknowledged') THEN CURRENT_TIMESTAMP
                        ELSE delivered_at
                    END,
                    acknowledged_at = CASE
                        WHEN %s = 'acknowledged' THEN CURRENT_TIMESTAMP
                        ELSE acknowledged_at
                    END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE case_id = %s AND state = 'claimed'
                RETURNING *
            """,
            (
                outcome.state,
                outcome.next_action,
                outcome.request_id,
                outcome.response_status,
                outcome.manual_package,
                outcome.evidence,
                outcome.retryable,
                outcome.failure_code,
                outcome.state,
                outcome.state,
                outcome.state,
                case_id,
            ),
        )
        row = cur.fetchone()
        if not row:
            raise VnbExchangeStoreError("VNB submission outcome was not recorded")
        row = dict(row)
        row["event_id"] = enqueue_event(
            cur,
            f"formation.submission.{outcome.state}",
            case_id,
            row["community_id"],
            {"case_id": case_id, "state": outcome.state},
        )
        return row


def get_case(community_id: str, case_id: str) -> dict | None:
    """Return one VNB case scoped to its community without private blobs."""
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
                SELECT case_id, community_id, adapter_key, contract_version,
                       capability_snapshot, payload_fingerprint, state,
                       next_action, response_status, retryable, failure_code,
                       created_by, delivered_by, created_at, attempted_at, delivered_at,
                       acknowledged_at, updated_at
                FROM vnb_submission_cases
                WHERE community_id = %s AND case_id = %s
            """,
            (community_id, case_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def mark_manual_delivered(
    community_id: str, case_id: str, actor_building_id: str
) -> dict:
    """Atomically confirm manual delivery and advance the formation state."""
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
                SELECT * FROM vnb_submission_cases
                WHERE community_id = %s AND case_id = %s
                FOR UPDATE
            """,
            (community_id, case_id),
        )
        case = cur.fetchone()
        if not case:
            raise VnbExchangeStoreError("VNB submission case was not found")
        case = dict(case)
        if case["state"] == "delivered":
            return case
        if case["state"] != "prepared" or not case.get("manual_package"):
            raise VnbExchangeStoreError(
                "VNB submission is not ready for manual delivery"
            )
        cur.execute(
            """
                UPDATE communities
                SET status = 'dso_submitted',
                    dso_submitted_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE community_id = %s AND status = 'signatures_pending'
            """,
            (community_id,),
        )
        if cur.rowcount != 1:
            raise VnbExchangeStoreError("Formation state does not permit submission")
        cur.execute(
            """
                UPDATE vnb_submission_cases
                SET state = 'delivered', delivered_by = %s,
                    delivered_at = CURRENT_TIMESTAMP,
                    next_action = 'await_acknowledgement',
                    updated_at = CURRENT_TIMESTAMP
                WHERE community_id = %s AND case_id = %s AND state = 'prepared'
                RETURNING *
            """,
            (actor_building_id, community_id, case_id),
        )
        row = cur.fetchone()
        if not row:
            raise VnbExchangeStoreError("Manual delivery was not recorded")
        row = dict(row)
        row["event_id"] = enqueue_event(
            cur,
            "formation.submission.delivered",
            case_id,
            community_id,
            {"case_id": case_id, "state": "delivered"},
        )
        return row


def get_manual_package(community_id: str, case_id: str) -> dict | None:
    """Return the private manual package for one community-scoped case."""
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
                SELECT case_id, community_id, manual_package
                FROM vnb_submission_cases
                WHERE community_id = %s AND case_id = %s
                  AND state = 'prepared' AND manual_package IS NOT NULL
            """,
            (community_id, case_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def list_cases(community_id: str, limit: int = 20) -> list[dict]:
    """List private-safe submission case summaries, newest first."""
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
                SELECT case_id, community_id, adapter_key, contract_version,
                       state, next_action, response_status, retryable,
                       failure_code, created_at, delivered_at, acknowledged_at
                FROM vnb_submission_cases
                WHERE community_id = %s
                ORDER BY created_at DESC
                LIMIT %s
            """,
            (community_id, max(1, min(int(limit), 100))),
        )
        return [dict(row) for row in cur.fetchall()]
