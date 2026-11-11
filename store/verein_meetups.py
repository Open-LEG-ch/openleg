# SPDX-License-Identifier: AGPL-3.0-or-later
"""Private meetup drafting, explicit publication and stable occurrence history."""

import uuid
from datetime import UTC, datetime

from psycopg2.extras import Json

import verein_events
from store import verein


def create_tables(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS verein_meetups (
            id UUID PRIMARY KEY, slug TEXT NOT NULL UNIQUE,
            content JSONB NOT NULL, status TEXT NOT NULL CHECK(status IN ('draft','scheduled','cancelled')),
            starts_at TIMESTAMPTZ NOT NULL, ends_at TIMESTAMPTZ NOT NULL,
            version INTEGER NOT NULL CHECK(version>0), updated_at TIMESTAMPTZ NOT NULL,
            reviewer_id UUID NOT NULL REFERENCES verein_reviewers(id),
            notes JSONB, notes_approved_at TIMESTAMPTZ, notes_evidence TEXT,
            CHECK(ends_at>starts_at)
        );
        CREATE TABLE IF NOT EXISTS verein_meetup_revisions (
            event_id UUID NOT NULL REFERENCES verein_meetups(id), version INTEGER NOT NULL,
            content JSONB NOT NULL, recorded_at TIMESTAMPTZ NOT NULL,
            reviewer_id UUID NOT NULL REFERENCES verein_reviewers(id),
            PRIMARY KEY(event_id,version)
        )
    """)


def save(token, data, *, identifier=None, version=None):
    try:
        content = verein_events.validated(data)
    except verein_events.EventInputError as exc:
        raise verein.VereinDenied(str(exc)) from None
    now = datetime.now(UTC)
    with verein.transaction_cursor() as cur:
        actor = verein.require_reviewer(cur, token)
        if identifier is None:
            identifier = str(uuid.uuid4())
            cur.execute(
                """INSERT INTO verein_meetups(id,slug,content,status,starts_at,ends_at,version,updated_at,reviewer_id)
                VALUES (%s,%s,%s,%s,%s,%s,1,%s,%s) ON CONFLICT (slug) DO NOTHING RETURNING version""",
                (
                    identifier,
                    content["slug"],
                    Json(content),
                    content["status"],
                    content["start"],
                    content["end"],
                    now,
                    actor["id"],
                ),
            )
            row = cur.fetchone()
        else:
            cur.execute(
                """UPDATE verein_meetups SET
                notes_approved_at=CASE WHEN content=%s THEN notes_approved_at ELSE NULL END,
                notes_evidence=CASE WHEN content=%s THEN notes_evidence ELSE NULL END,
                content=%s,status=%s,starts_at=%s,ends_at=%s,
                version=version+1,updated_at=%s,reviewer_id=%s
                WHERE id=%s AND slug=%s AND version=%s AND (status='draft' OR %s!='draft') RETURNING version""",
                (
                    Json(content),
                    Json(content),
                    Json(content),
                    content["status"],
                    content["start"],
                    content["end"],
                    now,
                    actor["id"],
                    identifier,
                    content["slug"],
                    version,
                    content["status"],
                ),
            )
            row = cur.fetchone()
        if not row:
            raise verein.VereinDenied(
                "Slug already exists, stale edit or published event cannot become a draft"
            )
        cur.execute(
            "INSERT INTO verein_meetup_revisions VALUES (%s,%s,%s,%s,%s)",
            (identifier, row["version"], Json(content), now, actor["id"]),
        )
    return identifier


def list_for_review(token):
    with verein.transaction_cursor() as cur:
        verein.require_reviewer(cur, token)
        cur.execute("SELECT * FROM verein_meetups ORDER BY starts_at DESC LIMIT 300")
        return [dict(row) for row in cur.fetchall()]


def published(slug=None):
    with verein.transaction_cursor() as cur:
        cur.execute(
            """SELECT id,slug,content,status,starts_at,ends_at,version,updated_at,notes,notes_approved_at
            FROM verein_meetups WHERE status!='draft' AND (%s IS NULL OR slug=%s) ORDER BY starts_at""",
            (slug, slug),
        )
        rows = [dict(row) for row in cur.fetchall()]
        return (rows[0] if rows else None) if slug else rows


def publish_notes(token, identifier, notes, evidence, *, version):
    if not isinstance(evidence, str) or not 3 <= len(evidence.strip()) <= 500:
        raise verein.VereinDenied("Actual approval evidence required")
    if (
        not isinstance(notes, dict)
        or set(notes) != set(verein_events.LOCALES)
        or any(
            not isinstance(value, str) or not 3 <= len(value.strip()) <= 20000
            for value in notes.values()
        )
    ):
        raise verein.VereinDenied("Notes required in all four languages")
    with verein.transaction_cursor() as cur:
        actor = verein.require_reviewer(cur, token)
        now = datetime.now(UTC)
        cur.execute(
            """UPDATE verein_meetups SET notes=%s,notes_approved_at=%s,notes_evidence=%s,
            version=version+1,updated_at=%s,reviewer_id=%s
            WHERE id=%s AND version=%s AND status='scheduled' AND ends_at<=%s RETURNING version""",
            (
                Json(notes),
                now,
                evidence.strip(),
                now,
                actor["id"],
                identifier,
                version,
                now,
            ),
        )
        row = cur.fetchone()
        if not row:
            raise verein.VereinDenied("A published past occurrence is required")
        cur.execute(
            "INSERT INTO verein_meetup_revisions VALUES (%s,%s,%s,%s,%s)",
            (
                identifier,
                row["version"],
                Json({"notes": notes, "approval": evidence.strip()}),
                now,
                actor["id"],
            ),
        )


def withdraw_notes(token, identifier, reason, *, version):
    if not isinstance(reason, str) or not 3 <= len(reason.strip()) <= 500:
        raise verein.VereinDenied("Withdrawal reason required")
    with verein.transaction_cursor() as cur:
        actor = verein.require_reviewer(cur, token)
        now = datetime.now(UTC)
        cur.execute(
            """UPDATE verein_meetups SET notes=NULL,notes_approved_at=NULL,notes_evidence=NULL,
            version=version+1,updated_at=%s,reviewer_id=%s WHERE id=%s AND version=%s AND notes IS NOT NULL RETURNING version""",
            (now, actor["id"], identifier, version),
        )
        row = cur.fetchone()
        if not row:
            raise verein.VereinDenied("Published notes required")
        cur.execute(
            "INSERT INTO verein_meetup_revisions VALUES (%s,%s,%s,%s,%s)",
            (
                identifier,
                row["version"],
                Json({"notes_withdrawn": reason.strip()}),
                now,
                actor["id"],
            ),
        )
