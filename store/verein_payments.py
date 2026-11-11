# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reviewed recipient versions. Withdrawal never reactivates an older account."""

import uuid
from datetime import UTC, datetime

from psycopg2.extras import Json

import verein_payments
from store import verein


def create_tables(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS verein_payment_profiles (
            id UUID PRIMARY KEY, version INTEGER NOT NULL UNIQUE CHECK(version>0),
            private_input JSONB NOT NULL, public_snapshot JSONB NOT NULL,
            prepared_by UUID NOT NULL REFERENCES verein_reviewers(id),
            prepared_at TIMESTAMPTZ NOT NULL,
            approved_by UUID REFERENCES verein_reviewers(id), approved_at TIMESTAMPTZ,
            approval_evidence TEXT, qr_review_evidence TEXT, withdrawn_by UUID REFERENCES verein_reviewers(id),
            withdrawn_at TIMESTAMPTZ, withdrawal_reason TEXT,
            CHECK(approved_by IS NULL OR approved_by!=prepared_by),
            CHECK((approved_by IS NULL)=(approved_at IS NULL))
        )
    """)


def prepare(token, data):
    private, public = verein_payments.validated(data)
    with verein.transaction_cursor() as cur:
        actor = verein.require_reviewer(cur, token)
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('verein-payment-profiles'))")
        cur.execute(
            "SELECT COALESCE(MAX(version),0)+1 AS next FROM verein_payment_profiles"
        )
        version = cur.fetchone()["next"]
        identifier = str(uuid.uuid4())
        cur.execute(
            """INSERT INTO verein_payment_profiles
            (id,version,private_input,public_snapshot,prepared_by,prepared_at)
            VALUES (%s,%s,%s,%s,%s,%s)""",
            (
                identifier,
                version,
                Json(private),
                Json(public),
                actor["id"],
                datetime.now(UTC),
            ),
        )
        return identifier


def review(token, identifier=None):
    with verein.transaction_cursor() as cur:
        verein.require_reviewer(cur, token)
        if identifier:
            cur.execute(
                "SELECT * FROM verein_payment_profiles WHERE id::text=%s", (identifier,)
            )
            row = cur.fetchone()
            return dict(row) if row else None
        cur.execute(
            "SELECT id,version,prepared_at,approved_at,withdrawn_at FROM verein_payment_profiles ORDER BY version DESC LIMIT 300"
        )
        return [dict(row) for row in cur.fetchall()]


def approve(token, identifier, evidence, qr_evidence):
    evidence = verein_payments.evidence_reference(evidence, "evidence")
    qr_evidence = verein_payments.evidence_reference(qr_evidence, "qr_evidence")
    with verein.transaction_cursor() as cur:
        actor = verein.require_reviewer(cur, token)
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('verein-payment-profiles'))")
        cur.execute(
            "SELECT * FROM verein_payment_profiles WHERE id::text=%s FOR UPDATE",
            (identifier,),
        )
        row = cur.fetchone()
        if not row or row["approved_at"] or row["prepared_by"] == actor["id"]:
            raise verein.VereinDenied("Independent reviewer required")
        cur.execute("SELECT MAX(version) AS latest FROM verein_payment_profiles")
        if cur.fetchone()["latest"] != row["version"]:
            raise verein.VereinDenied(
                "Only the latest recipient candidate may be approved"
            )
        cur.execute(
            "UPDATE verein_payment_profiles SET approved_by=%s,approved_at=%s,approval_evidence=%s,qr_review_evidence=%s WHERE id=%s",
            (actor["id"], datetime.now(UTC), evidence, qr_evidence, identifier),
        )


def withdraw(token, identifier, reason):
    reason = verein_payments.evidence_reference(reason, "reason")
    with verein.transaction_cursor() as cur:
        actor = verein.require_reviewer(cur, token)
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('verein-payment-profiles'))")
        cur.execute(
            """UPDATE verein_payment_profiles SET withdrawn_by=%s,withdrawn_at=%s,withdrawal_reason=%s
            WHERE id::text=%s AND id=(SELECT id FROM verein_payment_profiles WHERE approved_at IS NOT NULL ORDER BY version DESC LIMIT 1) AND approved_at IS NOT NULL AND withdrawn_at IS NULL RETURNING id""",
            (actor["id"], datetime.now(UTC), reason, identifier),
        )
        if not cur.fetchone():
            raise verein.VereinDenied("An active approved profile is required")


def active():
    with verein.transaction_cursor() as cur:
        cur.execute("""SELECT id,version,public_snapshot,approved_at,withdrawn_at
            FROM verein_payment_profiles WHERE approved_at IS NOT NULL ORDER BY version DESC LIMIT 1""")
        row = cur.fetchone()
        if not row or row["withdrawn_at"]:
            return None
        return {
            "id": str(row["id"]),
            "version": row["version"],
            "approved_at": row["approved_at"],
            **row["public_snapshot"],
        }
