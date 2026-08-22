# SPDX-License-Identifier: AGPL-3.0-or-later
"""Repository for hashed, single-use access tokens, both kinds.

The dashboard and the municipality tables were served by two modules holding
the same three statements under a different noun, so a fix to the atomic
consume or the revoke guard could land in one and never reach the other. Each
statement is written once here. The table and the subject column come from the
module constants below and never from a caller, so no argument can steer a
statement at another table.
"""

import logging

logger = logging.getLogger(__name__)

_DASHBOARD_TABLE = "dashboard_access_tokens"
_DASHBOARD_COLUMN = "building_id"
_MUNICIPALITY_TABLE = "municipality_access_tokens"
_MUNICIPALITY_COLUMN = "municipality_id"


def _get_connection():
    import database

    return database.get_connection()


def _save_token(table: str, column: str, token_hash: str, subject, ttl_seconds) -> bool:
    """Persist a precomputed SHA-256 token hash with its expiry."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                    INSERT INTO {table} (
                        token_hash, {column}, expires_at
                    )
                    VALUES (
                        %s,
                        %s,
                        CURRENT_TIMESTAMP + (%s * INTERVAL '1 second')
                    )
                    ON CONFLICT (token_hash) DO NOTHING
                """,
                (token_hash, subject, ttl_seconds),
            )
            return cur.rowcount > 0
    except Exception as e:
        logger.error(f"[DB] Error saving access token in {table}: {e}")
        return False


def _consume_token(table: str, column: str, token_hash: str) -> dict | None:
    """Atomically mark a token as used and return its subject if still valid."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                    UPDATE {table}
                    SET used_at = CURRENT_TIMESTAMP
                    WHERE token_hash = %s
                      AND expires_at > CURRENT_TIMESTAMP
                      AND used_at IS NULL
                      AND revoked_at IS NULL
                    RETURNING {column}
                """,
                (token_hash,),
            )
            row = cur.fetchone()
            if row:
                return dict(row)
            return None
    except Exception as e:
        logger.error(f"[DB] Error consuming access token in {table}: {e}")
        return None


def _revoke_tokens(table: str, column: str, subject) -> int:
    """Revoke every unused token issued for one subject."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                    UPDATE {table}
                    SET revoked_at = CURRENT_TIMESTAMP
                    WHERE {column} = %s
                      AND used_at IS NULL
                      AND revoked_at IS NULL
                """,
                (subject,),
            )
            return cur.rowcount
    except Exception as e:
        logger.error(f"[DB] Error revoking access tokens in {table}: {e}")
        return 0


def save_dashboard_access_token(
    token_hash: str, building_id: str, ttl_seconds: int
) -> bool:
    """Persist a precomputed SHA-256 token hash with its expiry."""
    return _save_token(
        _DASHBOARD_TABLE, _DASHBOARD_COLUMN, token_hash, building_id, ttl_seconds
    )


def consume_dashboard_access_token(token_hash: str) -> dict | None:
    """Atomically mark a token as used and return its building if still valid."""
    return _consume_token(_DASHBOARD_TABLE, _DASHBOARD_COLUMN, token_hash)


def revoke_dashboard_access_tokens(building_id: str) -> int:
    """Revoke all unused dashboard access tokens for a building."""
    return _revoke_tokens(_DASHBOARD_TABLE, _DASHBOARD_COLUMN, building_id)


def save_municipality_access_token(
    token_hash: str, municipality_id: int, ttl_seconds: int
) -> bool:
    """Persist a precomputed SHA-256 token hash with its expiry."""
    return _save_token(
        _MUNICIPALITY_TABLE,
        _MUNICIPALITY_COLUMN,
        token_hash,
        municipality_id,
        ttl_seconds,
    )


def consume_municipality_access_token(token_hash: str) -> dict | None:
    """Atomically mark a token as used and return its municipality if still valid."""
    return _consume_token(_MUNICIPALITY_TABLE, _MUNICIPALITY_COLUMN, token_hash)


def revoke_municipality_access_tokens(municipality_id: int) -> int:
    """Revoke all unused municipality access tokens for a municipality."""
    return _revoke_tokens(_MUNICIPALITY_TABLE, _MUNICIPALITY_COLUMN, municipality_id)
