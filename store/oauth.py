# SPDX-License-Identifier: AGPL-3.0-or-later
"""OAuth grants and single-use authorization codes."""

from psycopg2.extras import Json

import community_access


class AdminLookupUnavailable(RuntimeError):
    """The approving administrator could not be checked."""


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
    refresh_hash,
):
    # Release the read connection before checking membership. The community
    # lookup must not need a second connection while the OAuth one is held.
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT payload FROM oauth_codes WHERE code_hash=%s
            AND expires_at > CURRENT_TIMESTAMP
            AND payload->>'client_id'=%s AND payload->>'redirect_uri'=%s
            AND payload->>'code_challenge'=%s AND payload->>'resource'=%s""",
            (code_hash, client_id, redirect_uri, challenge, resource),
        )
        row = cur.fetchone()
    if not row or not approving_admin_valid(row["payload"]):
        return None
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
            """INSERT INTO oauth_grants(id,token_hash,payload,downstream_hash)
            VALUES (%s,%s,%s,%s)""",
            (grant_id, token_hash, Json(p), downstream_hash),
        )
        cur.execute(
            "INSERT INTO oauth_refresh_tokens(token_hash,grant_id) VALUES (%s,%s)",
            (refresh_hash, grant_id),
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
    if not row or not approving_admin_valid(row["payload"]):
        return None
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE oauth_grants SET last_used_at=CURRENT_TIMESTAMP WHERE id=%s",
            (row["id"],),
        )
    return dict(row)


def _revoke(cur, grant_id):
    cur.execute(
        "UPDATE oauth_grants SET revoked_at=COALESCE(revoked_at,CURRENT_TIMESTAMP) WHERE id=%s",
        (grant_id,),
    )
    cur.execute(
        "UPDATE operator_api_clients SET active=FALSE,updated_at=CURRENT_TIMESTAMP WHERE id=%s",
        (grant_id,),
    )


def refresh(
    token_hash, client_id, resource, scope, access_hash, refresh_hash, downstream_hash
):
    with _get_connection() as conn, conn.cursor() as cur:
        # Every refresh and dashboard revocation locks the grant first. Retain
        # consumed hashes so a replay can revoke the entire token family.
        cur.execute(
            """SELECT g.*, g.expires_at>CURRENT_TIMESTAMP AS unexpired FROM oauth_grants g
            JOIN oauth_refresh_tokens r ON r.grant_id=g.id
            WHERE r.token_hash=%s FOR UPDATE OF g""",
            (token_hash,),
        )
        grant = cur.fetchone()
        if not grant:
            return None
        p = grant["payload"]
        if p["client_id"] != client_id or p["resource"] != resource:
            return None
        cur.execute(
            "SELECT * FROM oauth_refresh_tokens WHERE token_hash=%s", (token_hash,)
        )
        token = cur.fetchone()
        if token["used_at"] is not None:
            _revoke(cur, grant["id"])
            return None
        if (
            grant["revoked_at"] is not None
            or not grant["unexpired"]
            or not approving_admin_valid(p)
        ):
            return None
        cur.execute(
            """SELECT id FROM operator_api_clients
            WHERE id=%s AND active=TRUE AND token_hash=%s FOR UPDATE""",
            (grant["id"], grant["downstream_hash"]),
        )
        if not cur.fetchone():
            return None
        if scope is not None and set(scope.split()) != set(p["scope"].split()):
            return None
        cur.execute(
            """UPDATE oauth_refresh_tokens SET used_at=CURRENT_TIMESTAMP
            WHERE token_hash=%s AND expires_at>CURRENT_TIMESTAMP RETURNING expires_at""",
            (token_hash,),
        )
        valid = cur.fetchone()
        if not valid:
            return None
        # Absolute family lifetime: rotation never extends the original expiry.
        cur.execute(
            """INSERT INTO oauth_refresh_tokens(token_hash,grant_id,expires_at)
            VALUES (%s,%s,%s)""",
            (refresh_hash, grant["id"], valid["expires_at"]),
        )
        cur.execute(
            """UPDATE oauth_grants SET token_hash=%s,downstream_hash=%s,
            expires_at=CURRENT_TIMESTAMP + INTERVAL '300 seconds',
            last_used_at=CURRENT_TIMESTAMP WHERE id=%s""",
            (access_hash, downstream_hash, grant["id"]),
        )
        cur.execute(
            "UPDATE operator_api_clients SET token_hash=%s,updated_at=CURRENT_TIMESTAMP WHERE id=%s",
            (downstream_hash, grant["id"]),
        )
        return p


def list_grants(community_id):
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT g.id,c.community_id,g.payload->>'scope' AS scope,
            g.payload->>'client_id' AS client_id,g.payload->>'name' AS name,
            g.created_at,g.last_used_at,g.revoked_at,c.active
            FROM oauth_grants g JOIN operator_api_clients c ON c.id=g.id
            WHERE c.community_id=%s ORDER BY g.created_at DESC""",
            (community_id,),
        )
        return [dict(row) for row in cur.fetchall()]


def revoke(community_id, grant_id):
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT g.id FROM oauth_grants g
            JOIN operator_api_clients c ON c.id=g.id
            WHERE g.id=%s AND c.community_id=%s FOR UPDATE OF g""",
            (grant_id, community_id),
        )
        if not cur.fetchone():
            return False
        _revoke(cur, grant_id)
        return True


def approving_admin_valid(payload):
    # Community presentation lookups swallow DB errors as None. Read the own
    # membership directly so missing membership and unavailable storage differ.
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT role, access_roles, status FROM community_members
                WHERE community_id=%s AND building_id=%s""",
                (payload["community_id"], payload["building_id"]),
            )
            member = cur.fetchone()
    except Exception as exc:
        raise AdminLookupUnavailable("Administrator lookup unavailable") from exc
    return bool(
        member
        and community_access.is_administrator(member)
        and member["status"] == "confirmed"
    )


def credential_grant_valid(client_id):
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT payload, revoked_at IS NULL AND
            expires_at > CURRENT_TIMESTAMP AS valid FROM oauth_grants WHERE id=%s""",
            (client_id,),
        )
        row = cur.fetchone()
    try:
        return row is None or (row["valid"] and approving_admin_valid(row["payload"]))
    except AdminLookupUnavailable:
        return False
