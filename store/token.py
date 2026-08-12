# SPDX-License-Identifier: AGPL-3.0-or-later
"""Verification/unsubscribe token repository.

Repository module for the verification/unsubscribe token domain: save, fetch,
use, and delete entries in the ``tokens`` table. The connection seam is
resolved via ``database.get_connection`` at call time so existing tests that
``monkeypatch.setattr(database, "get_connection", ...)`` keep working
unchanged and ``database`` can re-export these functions for legacy callers.
"""

import logging

logger = logging.getLogger(__name__)


def _get_connection():
    import database

    return database.get_connection()


# === Token Operations ===


def save_token(
    token: str, building_id: str, token_type: str, ttl_seconds: int = 2592000
) -> bool:
    """Save a verification or unsubscribe token (default TTL: 30 days)."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    INSERT INTO tokens (token, building_id, token_type, expires_at)
                    VALUES (
                        %s, %s, %s,
                        CURRENT_TIMESTAMP + (%s * INTERVAL '1 second')
                    )
                    ON CONFLICT (token) DO UPDATE SET
                        building_id = EXCLUDED.building_id,
                        token_type = EXCLUDED.token_type,
                        expires_at = EXCLUDED.expires_at
                """,
                (token, building_id, token_type, ttl_seconds),
            )
            return True
    except Exception as e:
        logger.error(f"[DB] Error saving token: {e}")
        return False


def get_token(token: str) -> dict | None:
    """Get token info if valid (not expired, not used)."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT * FROM tokens
                    WHERE token = %s
                    AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
                    AND used_at IS NULL
                """,
                (token,),
            )
            row = cur.fetchone()
            if row:
                return dict(row)
            return None
    except Exception as e:
        logger.error(f"[DB] Error getting token: {e}")
        return None


def use_token(token: str) -> bool:
    """Mark a token as used. Returns True if an unused, unexpired row was updated."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    UPDATE tokens SET used_at = CURRENT_TIMESTAMP
                    WHERE token = %s
                      AND used_at IS NULL
                      AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
                """,
                (token,),
            )
            return cur.rowcount > 0
    except Exception as e:
        logger.error(f"[DB] Error using token: {e}")
        return False


def delete_tokens_for_building(building_id: str, token_type: str | None = None) -> int:
    """Delete tokens for a building."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            if token_type:
                cur.execute(
                    """
                        DELETE FROM tokens
                        WHERE building_id = %s AND token_type = %s
                    """,
                    (building_id, token_type),
                )
            else:
                cur.execute(
                    """
                        DELETE FROM tokens WHERE building_id = %s
                    """,
                    (building_id,),
                )
            return cur.rowcount
    except Exception as e:
        logger.error(f"[DB] Error deleting tokens: {e}")
        return 0
