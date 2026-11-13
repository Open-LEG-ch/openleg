# SPDX-License-Identifier: AGPL-3.0-or-later
"""OAuth grants and single-use authorization codes."""

from psycopg2.extras import Json


def _get_connection():
    import database

    return database.get_connection()


def save_code(code_hash, payload):
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO oauth_codes(code_hash,payload) VALUES (%s,%s)",
            (code_hash, Json(payload)),
        )


def redeem(
    code_hash,
    client_id,
    redirect_uri,
    challenge,
    resource,
    grant_id,
    token_hash,
    downstream_hash,
):
    # The conditional DELETE and grant creation share one transaction.
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """DELETE FROM oauth_codes WHERE code_hash=%s
            AND expires_at > CURRENT_TIMESTAMP
            AND payload->>'client_id'=%s AND payload->>'redirect_uri'=%s
            AND payload->>'code_challenge'=%s AND payload->>'resource'=%s
            RETURNING payload""",
            (code_hash, client_id, redirect_uri, challenge, resource),
        )
        row = cur.fetchone()
        if not row:
            return None
        p = row["payload"]
        if not approving_admin_valid(p):
            return None
        # Each grant adds a live credential, so it obeys the per-community cap.
        # At the limit this raises and rolls back, leaving the code unused.
        from store.operator_api import (
            _check_credential_capacity,
            _lock_credential_community,
        )

        _lock_credential_community(cur, p["community_id"])
        _check_credential_capacity(cur, p["community_id"])
        cur.execute(
            """INSERT INTO operator_api_clients
            (id,community_id,created_by,name,capabilities,token_hash,actor_kind,agent_label)
            VALUES (%s,%s,%s,%s,%s,%s,'agent',%s)""",
            (
                grant_id,
                p["community_id"],
                p["building_id"],
                p["name"],
                Json(p["capabilities"]),
                downstream_hash,
                p["name"],
            ),
        )
        cur.execute(
            """INSERT INTO oauth_grants(id,token_hash,payload)
            VALUES (%s,%s,%s)""",
            (grant_id, token_hash, Json(p)),
        )
        return p


def introspect(token_hash):
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT g.id,g.payload,extract(epoch FROM g.expires_at)::bigint AS exp,
                      c.token_hash AS downstream_hash
            FROM oauth_grants g JOIN operator_api_clients c ON c.id=g.id
            WHERE g.token_hash=%s AND g.revoked_at IS NULL
            AND g.expires_at>CURRENT_TIMESTAMP AND c.active=TRUE""",
            (token_hash,),
        )
        row = cur.fetchone()
        return dict(row) if row and approving_admin_valid(row["payload"]) else None


def approving_admin_valid(payload):
    from operator_api import _admin_for

    try:
        return _admin_for(payload["community_id"], payload["building_id"])
    except Exception:
        return False


def credential_grant_valid(client_id):
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT payload, revoked_at IS NULL AND
            expires_at > CURRENT_TIMESTAMP AS valid FROM oauth_grants WHERE id=%s""",
            (client_id,),
        )
        row = cur.fetchone()
    return row is None or (row["valid"] and approving_admin_valid(row["payload"]))
