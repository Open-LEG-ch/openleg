# SPDX-License-Identifier: AGPL-3.0-or-later
"""Immutable private report inputs and independently approved public snapshots."""

import hashlib
import json
import uuid
from datetime import UTC, datetime

from psycopg2.extras import Json

import verein_finance
from i18n import gettext as _
from store import verein


def create_tables(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS verein_finance_reports (
            id UUID PRIMARY KEY, quarter TEXT NOT NULL, version INTEGER NOT NULL,
            input_digest TEXT NOT NULL, private_input JSONB NOT NULL,
            public_snapshot JSONB NOT NULL, correction_reason TEXT NOT NULL,
            prepared_by UUID NOT NULL REFERENCES verein_reviewers(id),
            prepared_at TIMESTAMPTZ NOT NULL,
            reviewed_by UUID REFERENCES verein_reviewers(id),
            review_evidence TEXT, published_at TIMESTAMPTZ,
            UNIQUE(quarter,version), UNIQUE(quarter,input_digest),
            CHECK(reviewed_by IS NULL OR reviewed_by!=prepared_by),
            CHECK((published_at IS NULL)=(reviewed_by IS NULL))
        );
        CREATE TABLE IF NOT EXISTS verein_finance_sources (
            source_id TEXT PRIMARY KEY, quarter TEXT NOT NULL,
            public_id UUID NOT NULL UNIQUE
        )
    """)


def _continuity(cur, private):
    """A correction cannot silently invalidate another published quarter."""
    cur.execute(
        """SELECT DISTINCT ON (quarter) quarter,private_input
        FROM verein_finance_reports WHERE published_at IS NOT NULL
        ORDER BY quarter,version DESC"""
    )
    verein_finance.validate_continuity(
        private, {row["quarter"]: row["private_input"] for row in cur.fetchall()}
    )


def prepare(token, data):
    private, public = verein_finance.reconcile(data)
    try:
        reason = verein_finance.text(data.get("correction_reason", ""), required=False)
    except verein_finance.FinanceInputError as exc:
        exc.field = "correction_reason"
        raise
    digest = hashlib.sha256(
        json.dumps([private, reason], sort_keys=True).encode()
    ).hexdigest()
    with verein.transaction_cursor(
        policy_errors=(verein_finance.FinanceInputError,)
    ) as cur:
        actor = verein.require_reviewer(cur, token)
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('verein-finance-reports'))")
        _continuity(cur, private)
        cur.execute(
            "SELECT id FROM verein_finance_reports WHERE quarter=%s AND input_digest=%s",
            (public["quarter"], digest),
        )
        previous = cur.fetchone()
        if previous:
            return str(previous["id"])
        cur.execute(
            "SELECT version,private_input FROM verein_finance_reports WHERE quarter=%s ORDER BY version DESC LIMIT 1",
            (public["quarter"],),
        )
        last = cur.fetchone()
        if last and not reason:
            raise verein_finance.FinanceInputError(
                _("Eine neue Version braucht eine öffentliche Korrekturbegründung."),
                field="correction_reason",
            )
        cur.execute(
            "SELECT private_input FROM verein_finance_reports WHERE quarter=%s AND published_at IS NOT NULL ORDER BY version DESC LIMIT 1",
            (public["quarter"],),
        )
        published = cur.fetchone()
        if published and not {
            row["source_id"] for row in published["private_input"]["bookings"]
        }.issubset({row["source_id"] for row in private["bookings"]}):
            raise verein_finance.FinanceInputError(
                _(
                    "Bereits veröffentlichte Quellbelege dürfen nicht verschwinden. Erfassen Sie eine nachvollziehbare Korrektur."
                ),
                field="bookings",
            )
        for source, booking in zip(
            private["bookings"], public["bookings"], strict=True
        ):
            cur.execute(
                "INSERT INTO verein_finance_sources VALUES (%s,%s,%s) ON CONFLICT (source_id) DO NOTHING",
                (source["source_id"], public["quarter"], str(uuid.uuid4())),
            )
            cur.execute(
                "SELECT quarter,public_id FROM verein_finance_sources WHERE source_id=%s",
                (source["source_id"],),
            )
            identity = cur.fetchone()
            if identity["quarter"] != public["quarter"]:
                raise verein_finance.FinanceInputError(
                    _(
                        "Ein Quellbeleg gehört bereits zu einem anderen Quartal. Importieren Sie denselben Beleg nicht nochmals."
                    ),
                    field="bookings",
                )
            booking["id"] = str(identity["public_id"])
        identifier = str(uuid.uuid4())
        now = datetime.now(UTC)
        public.update(
            version=last["version"] + 1 if last else 1,
            correction_reason=reason,
            prepared_at=now.isoformat(),
        )
        cur.execute(
            """INSERT INTO verein_finance_reports
            (id,quarter,version,input_digest,private_input,public_snapshot,correction_reason,prepared_by,prepared_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                identifier,
                public["quarter"],
                public["version"],
                digest,
                Json(private),
                Json(public),
                reason,
                actor["id"],
                now,
            ),
        )
        return identifier


def for_review(token, identifier=None):
    with verein.transaction_cursor(
        policy_errors=(verein_finance.FinanceInputError,)
    ) as cur:
        verein.require_reviewer(cur, token)
        if identifier:
            cur.execute(
                "SELECT * FROM verein_finance_reports WHERE id::text=%s", (identifier,)
            )
            row = cur.fetchone()
            return dict(row) if row else None
        cur.execute(
            "SELECT id,quarter,version,prepared_at,published_at FROM verein_finance_reports ORDER BY quarter DESC,version DESC LIMIT 300"
        )
        return [dict(row) for row in cur.fetchall()]


def publish(token, identifier, evidence):
    try:
        evidence = verein_finance.text(evidence)
    except verein_finance.FinanceInputError as exc:
        raise verein.VereinDenied(str(exc)) from None
    with verein.transaction_cursor(
        policy_errors=(verein_finance.FinanceInputError,)
    ) as cur:
        actor = verein.require_reviewer(cur, token)
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('verein-finance-reports'))")
        cur.execute(
            "SELECT * FROM verein_finance_reports WHERE id::text=%s FOR UPDATE",
            (identifier,),
        )
        report = cur.fetchone()
        if not report or report["published_at"] or report["prepared_by"] == actor["id"]:
            raise verein.VereinDenied(
                "A different active reviewer must approve an unpublished report"
            )
        cur.execute(
            "SELECT MAX(version) AS latest FROM verein_finance_reports WHERE quarter=%s",
            (report["quarter"],),
        )
        if cur.fetchone()["latest"] != report["version"]:
            raise verein.VereinDenied("Review the latest prepared version")
        _continuity(cur, report["private_input"])
        now = datetime.now(UTC)
        snapshot = dict(report["public_snapshot"], published_at=now.isoformat())
        cur.execute(
            "UPDATE verein_finance_reports SET public_snapshot=%s,reviewed_by=%s,review_evidence=%s,published_at=%s WHERE id=%s",
            (Json(snapshot), actor["id"], evidence, now, identifier),
        )
        return report["quarter"]


def published(quarter=None, version=None):
    """Select only the explicitly redacted snapshot; never private source records."""
    with verein.transaction_cursor(
        policy_errors=(verein_finance.FinanceInputError,)
    ) as cur:
        if quarter:
            cur.execute(
                """SELECT public_snapshot FROM verein_finance_reports
                WHERE published_at IS NOT NULL AND quarter=%s AND (%s IS NULL OR version=%s)
                ORDER BY version DESC LIMIT 1""",
                (quarter, version, version),
            )
            row = cur.fetchone()
            return row["public_snapshot"] if row else None
        cur.execute("""SELECT DISTINCT ON (quarter) public_snapshot FROM verein_finance_reports
            WHERE published_at IS NOT NULL ORDER BY quarter DESC,version DESC""")
        return [row["public_snapshot"] for row in cur.fetchall()]


def history(quarter):
    with verein.transaction_cursor(
        policy_errors=(verein_finance.FinanceInputError,)
    ) as cur:
        cur.execute(
            """SELECT version,correction_reason,published_at FROM verein_finance_reports
            WHERE quarter=%s AND published_at IS NOT NULL ORDER BY version DESC""",
            (quarter,),
        )
        return [dict(row) for row in cur.fetchall()]
