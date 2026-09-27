# SPDX-License-Identifier: AGPL-3.0-or-later
"""Private, mutual contact grants. All decisions and audit writes are atomic."""

import hashlib
import hmac
import json
import uuid
from contextlib import contextmanager


class ContactDenied(ValueError):
    """The participant, consent or transition is not eligible."""


class ContactStoreError(RuntimeError):
    """Contact storage is unavailable; never substitute public data."""


@contextmanager
def _cursor():
    import database

    try:
        with database.get_connection() as conn, conn.cursor() as cur:
            yield cur
    except ContactDenied:
        raise
    except Exception as exc:
        raise ContactStoreError("Contact storage unavailable") from exc


def create_tables(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS contact_requests (
            id UUID PRIMARY KEY,
            requester_id VARCHAR(64) NOT NULL REFERENCES buildings ON DELETE CASCADE,
            recipient_id VARCHAR(64) NOT NULL REFERENCES buildings ON DELETE CASCADE,
            status TEXT NOT NULL CHECK (status IN
                ('pending', 'accepted', 'declined', 'revoked', 'expired')),
            requester_identity TEXT NOT NULL,
            recipient_identity TEXT NOT NULL,
            requester_fields JSONB NOT NULL,
            recipient_fields JSONB NOT NULL DEFAULT '{}',
            requested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP + INTERVAL '30 days',
            accepted_at TIMESTAMPTZ,
            ended_at TIMESTAMPTZ,
            CHECK (requester_id <> recipient_id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS contact_pair_unique
            ON contact_requests (LEAST(requester_id, recipient_id),
                                 GREATEST(requester_id, recipient_id));
        CREATE INDEX IF NOT EXISTS contact_request_sender
            ON contact_requests (requester_id);
        CREATE INDEX IF NOT EXISTS contact_request_recipient
            ON contact_requests (recipient_id);
        CREATE TABLE IF NOT EXISTS contact_request_events (
            id BIGSERIAL PRIMARY KEY,
            request_id UUID NOT NULL REFERENCES contact_requests ON DELETE CASCADE,
            actor_id VARCHAR(64) REFERENCES buildings ON DELETE CASCADE,
            action TEXT NOT NULL,
            consent_fields JSONB NOT NULL DEFAULT '[]',
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS contact_event_request
            ON contact_request_events (request_id);
    """)


def _profile(cur, actor):
    cur.execute(
        """
        SELECT b.building_id, b.email, b.phone, b.verified, b.verified_at,
               b.verification_revision, b.bfs_number, c.share_with_neighbors,
               c.consent_timestamp
        FROM buildings b JOIN consents c USING (building_id)
        WHERE b.building_id = %s
    """,
        (actor,),
    )
    return cur.fetchone()


def _eligible(profile):
    return bool(
        profile
        and profile["verified"]
        and profile["bfs_number"]
        and profile["share_with_neighbors"]
    )


def _compatible(left, right):
    return (
        _eligible(left)
        and _eligible(right)
        and left["bfs_number"] == right["bfs_number"]
        and left["building_id"] != right["building_id"]
        and left["email"].lower() != right["email"].lower()
    )


def _identity(profile):
    # Changes to verification, municipality, contact data or discovery consent
    # invalidate an earlier grant, even when a profile is later re-enabled.
    return hashlib.sha256(
        json.dumps(dict(profile), sort_keys=True, default=str).encode()
    ).hexdigest()


def _handle(secret, actor, target):
    return hmac.new(
        str(secret).encode(),
        json.dumps([actor, target], separators=(",", ":")).encode(),
        hashlib.sha256,
    ).hexdigest()


def _candidates(cur, profile):
    if not _eligible(profile):
        return []
    cur.execute(
        """
        SELECT b.building_id FROM buildings b JOIN consents c USING (building_id)
        WHERE b.verified AND c.share_with_neighbors
          AND b.bfs_number = %s AND b.building_id <> %s
          AND LOWER(b.email) <> LOWER(%s)
          AND NOT EXISTS (
            SELECT 1 FROM contact_requests r
            WHERE (r.requester_id = %s AND r.recipient_id = b.building_id)
               OR (r.recipient_id = %s AND r.requester_id = b.building_id))
        ORDER BY b.building_id LIMIT 50
    """,
        (
            profile["bfs_number"],
            profile["building_id"],
            profile["email"],
            profile["building_id"],
            profile["building_id"],
        ),
    )
    return [r["building_id"] for r in cur.fetchall()]


def _fields(profile, fields):
    if not fields or not set(fields) <= {"email", "phone"}:
        raise ContactDenied()
    if any(not profile.get(field) for field in fields):
        raise ContactDenied()
    return {field: profile[field] for field in fields}


def _event(cur, rid, actor, action, fields=()):
    cur.execute(
        """
        INSERT INTO contact_request_events (request_id, actor_id, action, consent_fields)
        VALUES (%s, %s, %s, %s)
    """,
        (rid, actor, action, json.dumps(sorted(fields))),
    )


def _end(cur, row, status, actor=None):
    cur.execute(
        """
        UPDATE contact_requests SET status = %s, ended_at = CURRENT_TIMESTAMP,
            requester_fields = '{}', recipient_fields = '{}'
        WHERE id = %s
    """,
        (status, row["id"]),
    )
    _event(cur, row["id"], actor, status)
    row.update(status=status, requester_fields={}, recipient_fields={})


def _refresh(cur, row):
    left = _profile(cur, row["requester_id"])
    right = _profile(cur, row["recipient_id"])
    if row["status"] in {"pending", "accepted"}:
        if row["status"] == "pending" and row["past_expiry"]:
            _end(cur, row, "expired")
        elif (
            not _compatible(left, right)
            or _identity(left) != row["requester_identity"]
            or _identity(right) != row["recipient_identity"]
        ):
            _end(cur, row, "revoked")
    return left, right


def view(actor, secret):
    with _cursor() as cur:
        profile = _profile(cur, actor)
        if not profile:
            raise ContactDenied()
        cur.execute(
            """
            SELECT *, expires_at <= CURRENT_TIMESTAMP AS past_expiry
            FROM contact_requests WHERE requester_id = %s OR recipient_id = %s
            ORDER BY requested_at, id FOR UPDATE
        """,
            (actor, actor),
        )
        requests = []
        for row in cur.fetchall():
            _refresh(cur, row)
            incoming = row["recipient_id"] == actor
            requests.append(
                {
                    "id": str(row["id"]),
                    "status": row["status"],
                    "incoming": incoming,
                    "requested_at": row["requested_at"],
                    "expires_at": row["expires_at"],
                    "contact": (
                        row["requester_fields"] if incoming else row["recipient_fields"]
                    )
                    if row["status"] == "accepted"
                    else {},
                }
            )
        return {
            "eligible": _eligible(profile),
            "has_phone": bool(profile["phone"]),
            "candidates": [
                _handle(secret, actor, target) for target in _candidates(cur, profile)
            ],
            "requests": requests,
        }


def create(actor, candidate, fields, secret):
    if (
        not isinstance(candidate, str)
        or len(candidate) != 64
        or not candidate.isascii()
    ):
        raise ContactDenied()
    with _cursor() as cur:
        left = _profile(cur, actor)
        target = next(
            (
                bid
                for bid in _candidates(cur, left)
                if hmac.compare_digest(candidate, _handle(secret, actor, bid))
            ),
            None,
        )
        right = _profile(cur, target) if target else None
        if not _compatible(left, right):
            raise ContactDenied()
        approved = _fields(left, fields)
        rid = str(uuid.uuid4())
        cur.execute(
            """
            INSERT INTO contact_requests
                (id, requester_id, recipient_id, status, requester_identity,
                 recipient_identity, requester_fields)
            VALUES (%s, %s, %s, 'pending', %s, %s, %s)
            ON CONFLICT DO NOTHING RETURNING id
        """,
            (
                rid,
                actor,
                target,
                _identity(left),
                _identity(right),
                json.dumps(approved),
            ),
        )
        if not cur.fetchone():
            raise ContactDenied()
        _event(cur, rid, actor, "requested", fields)
        return [left["email"], right["email"]]


def transition(actor, rid, action, fields=()):
    try:
        rid = str(uuid.UUID(rid))
    except (ValueError, TypeError, AttributeError):
        raise ContactDenied() from None
    # Invalidated or expired requests must commit their terminal state even if
    # the attempted transition is rejected.
    recipients = None
    with _cursor() as cur:
        cur.execute(
            """
            SELECT *, expires_at <= CURRENT_TIMESTAMP AS past_expiry
            FROM contact_requests WHERE id = %s
              AND (requester_id = %s OR recipient_id = %s) FOR UPDATE
        """,
            (rid, actor, actor),
        )
        row = cur.fetchone()
        if not row:
            raise ContactDenied()
        left, right = _refresh(cur, row)
        pending_recipient = row["status"] == "pending" and row["recipient_id"] == actor
        if action == "accept" and pending_recipient:
            approved = _fields(right, fields)
            cur.execute(
                """
                UPDATE contact_requests SET status = 'accepted',
                    recipient_fields = %s, accepted_at = CURRENT_TIMESTAMP WHERE id = %s
            """,
                (json.dumps(approved), rid),
            )
            _event(cur, rid, actor, "accepted", fields)
            recipients = [left["email"], right["email"]]
        elif (action == "decline" and pending_recipient) or (
            action == "revoke" and row["status"] in {"pending", "accepted"}
        ):
            _end(cur, row, "declined" if action == "decline" else "revoked", actor)
            recipients = [left["email"], right["email"]]
    if recipients is None:
        raise ContactDenied()
    return recipients
