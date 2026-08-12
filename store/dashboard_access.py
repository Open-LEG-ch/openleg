# SPDX-License-Identifier: AGPL-3.0-or-later
"""Repository for hashed, single-use dashboard access tokens."""

import logging

logger = logging.getLogger(__name__)


def _get_connection():
    import database

    return database.get_connection()


def save_dashboard_access_token(
    token_hash: str, building_id: str, ttl_seconds: int
) -> bool:
    """Persist a precomputed SHA-256 token hash with its expiry."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    INSERT INTO dashboard_access_tokens (
                        token_hash, building_id, expires_at
                    )
                    VALUES (
                        %s,
                        %s,
                        CURRENT_TIMESTAMP + (%s * INTERVAL '1 second')
                    )
                    ON CONFLICT (token_hash) DO UPDATE SET
                        building_id = EXCLUDED.building_id,
                        expires_at = EXCLUDED.expires_at,
                        used_at = NULL,
                        revoked_at = NULL
                """,
                (token_hash, building_id, ttl_seconds),
            )
            return True
    except Exception as e:
        logger.error(f"[DB] Error saving dashboard access token: {e}")
        return False


def consume_dashboard_access_token(token_hash: str) -> dict | None:
    """Atomically mark a token as used and return its building if still valid."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    UPDATE dashboard_access_tokens
                    SET used_at = CURRENT_TIMESTAMP
                    WHERE token_hash = %s
                      AND expires_at > CURRENT_TIMESTAMP
                      AND used_at IS NULL
                      AND revoked_at IS NULL
                    RETURNING building_id
                """,
                (token_hash,),
            )
            row = cur.fetchone()
            if row:
                return dict(row)
            return None
    except Exception as e:
        logger.error(f"[DB] Error consuming dashboard access token: {e}")
        return None


def revoke_dashboard_access_tokens(building_id: str) -> int:
    """Revoke all unused dashboard access tokens for a building."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    UPDATE dashboard_access_tokens
                    SET revoked_at = CURRENT_TIMESTAMP
                    WHERE building_id = %s
                      AND used_at IS NULL
                      AND revoked_at IS NULL
                """,
                (building_id,),
            )
            return cur.rowcount
    except Exception as e:
        logger.error(f"[DB] Error revoking dashboard access tokens: {e}")
        return 0
