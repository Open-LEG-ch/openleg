# SPDX-License-Identifier: AGPL-3.0-or-later
"""Durable identities for agent requests; no domain submission on insertion."""

import uuid
from contextlib import contextmanager
from dataclasses import asdict

from psycopg2.extras import Json

import operator_actor
from store.vnb_exchange import VnbSubmissionConflict


def create_request(kind, command, client, preview):
    import database as db

    identity = (
        preview.payload_fingerprint if kind == "formation" else command.mutation_id
    )
    public_preview = {
        key: getattr(preview, key)
        for key in ("intended_action", "payload_fingerprint", "recipient")
    }
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO agent_confirmation_requests
            (request_id,community_id,client_id,kind,identity,command,canonical_payload,
             payload_fingerprint,adapter_key,preview,actor)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (community_id,kind,identity) DO NOTHING""",
            (
                str(uuid.uuid4()),
                command.community_id,
                client["id"],
                kind,
                identity,
                Json(asdict(command)),
                Json(preview.canonical_payload),
                preview.payload_fingerprint,
                preview.adapter_key,
                Json(public_preview),
                Json(operator_actor.snapshot()),
            ),
        )
        cur.execute(
            """SELECT * FROM agent_confirmation_requests
            WHERE community_id=%s AND kind=%s AND identity=%s""",
            (command.community_id, kind, identity),
        )
        row = dict(cur.fetchone())
        if (
            row["client_id"] != client["id"]
            or row["command"] != asdict(command)
            or row["payload_fingerprint"] != preview.payload_fingerprint
        ):
            raise VnbSubmissionConflict("Pending identity already bound")
        return row


def get_request(community_id, request_id):
    import database as db

    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM agent_confirmation_requests WHERE community_id=%s AND request_id=%s",
            (community_id, request_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def _expire(cur, row):
    from store.operator_api import enqueue_event

    cur.execute(
        "UPDATE agent_confirmation_requests SET state='expired' WHERE request_id=%s",
        (row["request_id"],),
    )
    enqueue_event(
        cur,
        row["kind"] + ".confirmation.expired",
        row["request_id"],
        row["community_id"],
        {"request_id": row["request_id"], "state": "expired"},
        actor=row["actor"],
    )
    row["state"] = "expired"


def expire_requests():
    import database as db

    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM agent_confirmation_requests WHERE state='awaiting_confirmation' AND expires_at<=clock_timestamp() ORDER BY request_id FOR UPDATE SKIP LOCKED"
        )
        rows = cur.fetchall()
        for row in rows:
            _expire(cur, row)
        return len(rows)


@contextmanager
def confirmation(community_id, request_id, building_id):
    """Serialize confirmation and expiry; hold authorization rows through execution."""
    import community_access
    import database as db

    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM agent_confirmation_requests WHERE community_id=%s AND request_id=%s FOR UPDATE",
            (community_id, request_id),
        )
        row = cur.fetchone()
        if row is None:
            raise LookupError
        cur.execute(
            "SELECT * FROM community_members WHERE community_id=%s AND building_id=%s FOR SHARE",
            (community_id, building_id),
        )
        member = cur.fetchone()
        if (
            not member
            or member["status"] != "confirmed"
            or not community_access.is_administrator(member)
        ):
            raise PermissionError
        if row["state"] == "awaiting_confirmation":
            cur.execute(
                "SELECT expires_at<=clock_timestamp() AS expired FROM agent_confirmation_requests WHERE request_id=%s",
                (request_id,),
            )
            if cur.fetchone()["expired"]:
                _expire(cur, row)
            else:
                cur.execute(
                    "SELECT * FROM operator_api_clients WHERE id=%s AND community_id=%s FOR SHARE",
                    (row["client_id"], community_id),
                )
                client = cur.fetchone()
                if (
                    not client
                    or not client["active"]
                    or client["actor_kind"] != "agent"
                    or row["kind"] + ".mutate" not in client["capabilities"]
                ):
                    raise PermissionError
        from store.vnb_exchange import confirmation_transaction

        with confirmation_transaction(conn):
            yield row
        if row["state"] == "confirmed":
            cur.execute(
                "UPDATE agent_confirmation_requests SET state='confirmed',confirmed_by=%s,result=%s WHERE request_id=%s",
                (row["confirmed_by"], Json(row["result"]), request_id),
            )
