# SPDX-License-Identifier: AGPL-3.0-or-later
"""Repository for hashed, single-use municipality access tokens."""

import logging

logger = logging.getLogger(__name__)


def _get_connection():
    import database

    return database.get_connection()


def save_municipality_access_token(
    token_hash: str, municipality_id: int, ttl_seconds: int
) -> bool:
    """Persist a precomputed SHA-256 token hash with its expiry."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    INSERT INTO municipality_access_tokens (
                        token_hash, municipality_id, expires_at
                    )
                    VALUES (
                        %s,
                        %s,
                        CURRENT_TIMESTAMP + (%s * INTERVAL '1 second')
                    )
                    ON CONFLICT (token_hash) DO NOTHING
                """,
                (token_hash, municipality_id, ttl_seconds),
            )
            return cur.rowcount > 0
    except Exception as e:
        logger.error(f"[DB] Error saving municipality access token: {e}")
        return False


def consume_municipality_access_token(token_hash: str) -> dict | None:
    """Atomically mark a token as used and return its municipality if still valid."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    UPDATE municipality_access_tokens
                    SET used_at = CURRENT_TIMESTAMP
                    WHERE token_hash = %s
                      AND expires_at > CURRENT_TIMESTAMP
                      AND used_at IS NULL
                      AND revoked_at IS NULL
                    RETURNING municipality_id
                """,
                (token_hash,),
            )
            row = cur.fetchone()
            if row:
                return dict(row)
            return None
    except Exception as e:
        logger.error(f"[DB] Error consuming municipality access token: {e}")
        return None


def revoke_municipality_access_tokens(municipality_id: int) -> int:
    """Revoke all unused municipality access tokens for a municipality."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    UPDATE municipality_access_tokens
                    SET revoked_at = CURRENT_TIMESTAMP
                    WHERE municipality_id = %s
                      AND used_at IS NULL
                      AND revoked_at IS NULL
                """,
                (municipality_id,),
            )
            return cur.rowcount
    except Exception as e:
        logger.error(f"[DB] Error revoking municipality access tokens: {e}")
        return 0
