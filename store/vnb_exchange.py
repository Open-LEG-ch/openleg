# SPDX-License-Identifier: AGPL-3.0-or-later
"""Persistence for versioned VNB submission cases."""

from __future__ import annotations

import uuid
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from typing import TYPE_CHECKING

from psycopg2.errors import UniqueViolation
from psycopg2.extras import Json

import community_access
import operator_actor
from operator_errors import ErrorCode
from store.operator_api import enqueue_event
from translation_markers import gettext_noop as _N

if TYPE_CHECKING:
    from vnb_exchange import (
        MutationClaim,
        MutationOutcome,
        SubmissionClaim,
        SubmissionOutcome,
    )


class VnbExchangeStoreError(RuntimeError):
    """A VNB submission case could not be persisted."""

    reason = ErrorCode.UNAVAILABLE


class VnbSubmissionConflict(VnbExchangeStoreError):
    """Another active submission prevents this formation attempt."""

    reason = ErrorCode.CONFLICT


_MUTATION_SELECT = """
    SELECT c.*, (
        SELECT e.event_id FROM operator_events e
        WHERE e.community_id=c.community_id AND e.aggregate_id=c.case_id
          AND e.event_type='membership.mutation.' || c.state
        ORDER BY e.occurred_at,e.event_id LIMIT 1
    ) AS event_id FROM vnb_mutation_cases c
"""


def get_mutation(community_id: str, mutation_id: str) -> dict | None:
    """Read one immutable command identity within its authorized community."""
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            _MUTATION_SELECT + "WHERE c.community_id = %s AND c.mutation_id = %s",
            (community_id, mutation_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def check_mutation(submission: MutationClaim) -> None:
    """Check existing identities and pending work without claiming anything."""
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT payload_fingerprint FROM vnb_mutation_cases
               WHERE community_id = %s AND mutation_id = %s""",
            (submission.community_id, submission.mutation_id),
        )
        existing = cur.fetchone()
        if existing:
            if existing["payload_fingerprint"] != submission.payload_fingerprint:
                raise VnbSubmissionConflict("Mutation identity has different content")
            return
        cur.execute(
            """SELECT 1 FROM vnb_mutation_cases
               WHERE community_id = %s AND participant_id = %s
                 AND state IN ('claimed', 'prepared', 'delivered') LIMIT 1""",
            (submission.community_id, submission.participant_id),
        )
        if cur.fetchone():
            raise VnbSubmissionConflict("Participant has a pending mutation")


def check_submission(submission: SubmissionClaim) -> None:
    """Check formation conflicts without the upsert that claims retries."""
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT contract_version, payload_fingerprint FROM vnb_submission_cases
               WHERE community_id = %s""",
            (submission.community_id,),
        )
        rows = cur.fetchall()
        if any(
            row["contract_version"] == submission.contract_version
            and row["payload_fingerprint"] == submission.payload_fingerprint
            for row in rows
        ):
            return
        if rows:
            raise VnbSubmissionConflict("Formation already has a different case")


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
            if existing["state"] != "claimed":
                # A locking SELECT can wait for a newer case version while its
                # statement snapshot still predates the event. Read again only
                # after holding the case lock, with a fresh READ COMMITTED snapshot.
                cur.execute(
                    _MUTATION_SELECT
                    + "WHERE c.community_id = %s AND c.mutation_id = %s",
                    (submission.community_id, submission.mutation_id),
                )
                return dict(cur.fetchone())
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
                   capability_snapshot, payload_fingerprint, actor, state, next_action
               ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                         %s, 'claimed', 'deliver') RETURNING *""",
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
                    Json(operator_actor.snapshot()),
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
                   evidence, retryable, failure_code, actor)
               SELECT case_id, %s, %s, %s, %s, %s, %s, actor
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
            actor=row.get("actor"),
        )
        return row


def list_mutations(community_id: str, limit: int = 100) -> list[dict]:
    """List the VNB projection with immutable LEG facts, scoped by community."""
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT case_id, mutation_id, community_id, participant_id, actor,
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
               SELECT case_id, %s, %s, %s, %s FROM vnb_mutation_cases
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


_confirmation_connection = ContextVar("vnb_confirmation_connection", default=None)


@contextmanager
def confirmation_transaction(connection):
    """Keep case, event, package and confirmation completion in one transaction."""
    token = _confirmation_connection.set(connection)
    try:
        yield
    finally:
        _confirmation_connection.reset(token)


def _get_connection():
    import database

    connection = _confirmation_connection.get()
    return (
        nullcontext(connection) if connection is not None else database.get_connection()
    )


def _guard_current_formation_bundle(cursor, submission):
    """Serialize a new package claim with document replacement before delivery."""
    if not submission.document_ids:
        return None
    cursor.execute(
        """SELECT 1 FROM vnb_submission_cases WHERE community_id = %s
           AND contract_version = %s AND payload_fingerprint = %s""",
        (
            submission.community_id,
            submission.contract_version,
            submission.payload_fingerprint,
        ),
    )
    if cursor.fetchone():
        return None
    changed = _N(
        "Die Dokumente oder Teilnehmenden wurden geändert. Prüfen Sie den aktuellen Entwurf."
    )
    cursor.execute(
        "SELECT status FROM communities WHERE community_id = %s FOR UPDATE",
        (submission.community_id,),
    )
    community = cursor.fetchone()
    cursor.execute(
        "SELECT * FROM vnb_submission_cases WHERE community_id = %s LIMIT 1",
        (submission.community_id,),
    )
    existing = cursor.fetchone()
    if existing:
        if (
            existing["contract_version"] == submission.contract_version
            and existing["payload_fingerprint"] == submission.payload_fingerprint
        ):
            return dict(existing)
        raise VnbSubmissionConflict(changed)
    if not community or community["status"] != "signatures_pending":
        raise VnbSubmissionConflict(changed)
    cursor.execute(
        """SELECT role, access_roles, status FROM community_members
           WHERE community_id = %s AND building_id = %s FOR SHARE""",
        (submission.community_id, submission.actor_building_id),
    )
    actor = cursor.fetchone()
    if (
        not actor
        or actor["status"] != "confirmed"
        or not community_access.allows(actor, community_access.MANAGE_DOCUMENTS)
    ):
        raise VnbSubmissionConflict(
            _N("Keine Berechtigung für die Netzbetreiber-Anmeldung.")
        )
    cursor.execute(
        """SELECT id, signing_status FROM leg_documents
           WHERE community_id = %s AND signing_status IS DISTINCT FROM 'superseded'
           ORDER BY id FOR SHARE""",
        (submission.community_id,),
    )
    documents = cursor.fetchall()
    if tuple(item["id"] for item in documents) != submission.document_ids or any(
        item["signing_status"] != "completed" for item in documents
    ):
        raise VnbSubmissionConflict(changed)
    cursor.execute(
        "SELECT 1 FROM leg_signature_receipts WHERE community_id = %s LIMIT 1",
        (submission.community_id,),
    )
    if cursor.fetchone():
        from store.formation_signatures import (
            SignatureConflict,
            SignatureDenied,
            locked_signature_bundle,
        )

        try:
            locked_signature_bundle(
                cursor, submission.community_id, submission.actor_building_id
            )
        except (SignatureConflict, SignatureDenied) as error:
            raise VnbSubmissionConflict(changed) from error
    return None


def claim(submission: SubmissionClaim) -> dict:
    """Claim one stable formation submission or return its existing case."""
    case_id = str(uuid.uuid4())
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            existing = _guard_current_formation_bundle(cur, submission)
            if existing is not None:
                return existing
            cur.execute(
                """
                INSERT INTO vnb_submission_cases (
                    case_id, community_id, created_by, adapter_key,
                    contract_version, payload_fingerprint,
                    capability_snapshot, actor, state, next_action
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'claimed', 'deliver')
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
                    Json(operator_actor.snapshot()),
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
            actor=row.get("actor"),
        )
        return row


def get_case(community_id: str, case_id: str) -> dict | None:
    """Return one VNB case scoped to its community without private blobs."""
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
                SELECT case_id, community_id, actor, adapter_key, contract_version,
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
                SELECT case_id, community_id, actor, adapter_key, contract_version,
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


def record_submission_response(community_id, case_id, state, evidence):
    """Accept one final response to a delivered package without approving start."""
    if state not in {"acknowledged", "rejected"} or not evidence:
        raise VnbExchangeStoreError("Invalid formation response")
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT * FROM vnb_submission_cases
               WHERE community_id = %s AND case_id = %s FOR UPDATE""",
            (community_id, case_id),
        )
        previous = cur.fetchone()
        if not previous:
            raise VnbExchangeStoreError("Formation submission was not found")
        if previous["state"] in {"acknowledged", "rejected"}:
            if (
                previous["state"] != state
                or not previous["response_evidence"]
                or bytes(previous["response_evidence"]) != evidence
            ):
                raise VnbSubmissionConflict("The formation response is final")
            return dict(previous)
        if previous["state"] != "delivered":
            raise VnbSubmissionConflict("Deliver the formation package first")
        cur.execute(
            """UPDATE vnb_submission_cases SET state = %s, response_status = %s,
                      response_evidence = %s, next_action = %s,
                      acknowledged_at = CASE WHEN %s = 'acknowledged'
                          THEN CURRENT_TIMESTAMP ELSE acknowledged_at END,
                      updated_at = CURRENT_TIMESTAMP
               WHERE community_id = %s AND case_id = %s RETURNING *""",
            (
                state,
                state,
                evidence,
                "none" if state == "acknowledged" else "review_rejection",
                state,
                community_id,
                case_id,
            ),
        )
        result = dict(cur.fetchone())
        result["event_id"] = enqueue_event(
            cur,
            "formation.submission." + state,
            case_id,
            community_id,
            {"case_id": case_id, "state": state},
        )
        return result
