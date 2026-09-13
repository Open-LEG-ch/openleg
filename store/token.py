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


class VerificationConflict(Exception):
    """Interest confirmation could not complete its write.

    Raised instead of returning ``None`` so the enclosing transaction fails
    loudly and rolls back; an invalid or stale token only ever yields
    ``None`` from :func:`confirm_building_interest`.
    """


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


def confirm_profile_deletion(token: str) -> bool:
    """Delete the profile for a valid unsubscribe token in one transaction."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT building_id FROM tokens
                    WHERE token = %s
                      AND token_type = 'unsubscribe'
                      AND used_at IS NULL
                      AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
                    FOR UPDATE
                """,
                (token,),
            )
            row = cur.fetchone()
            if not row:
                return False
            cur.execute(
                "DELETE FROM buildings WHERE building_id = %s",
                (row["building_id"],),
            )
            return cur.rowcount > 0
    except Exception:
        logger.exception("[DB] Error confirming profile deletion")
        return False


def confirm_building_interest(token: str) -> dict | None:
    """Verify the building bound to a revision-pinned verification token.

    Building-first locking: read the token's building without a lock, lock
    that building ``FOR UPDATE``, consume the token with
    building/revision/type/unused/expiry predicates, then mark the building
    verified. Returns the locked building snapshot, updated as verified, or
    ``None`` for invalid, replayed, wrong-building, stale-revision, expired,
    or missing tokens. A used or missing row is only ever ``None``; a failed
    write raises :class:`VerificationConflict`.
    """
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT building_id FROM tokens WHERE token = %s", (token,))
            row = cur.fetchone()
            if not row:
                return None
            building_id = row["building_id"]

            cur.execute(
                """
                    SELECT b.*, c.share_with_neighbors, c.share_with_utility,
                           c.updates_opt_in, c.consent_version
                    FROM buildings b
                    LEFT JOIN consents c ON b.building_id = c.building_id
                    WHERE b.building_id = %s
                    FOR UPDATE OF b
                """,
                (building_id,),
            )
            building = cur.fetchone()
            if not building:
                return None

            cur.execute(
                """
                    UPDATE tokens SET used_at = CURRENT_TIMESTAMP
                    WHERE token = %s
                      AND building_id = %s
                      AND token_type = 'verification'
                      AND verification_revision = %s
                      AND used_at IS NULL
                      AND (expires_at IS NULL OR expires_at > clock_timestamp())
                """,
                (token, building_id, building["verification_revision"]),
            )
            if cur.rowcount != 1:
                return None

            cur.execute(
                """
                    UPDATE buildings
                       SET verified = TRUE,
                           verified_at = CURRENT_TIMESTAMP,
                           updated_at = CURRENT_TIMESTAMP
                     WHERE building_id = %s
                   RETURNING verified_at
                """,
                (building_id,),
            )
            updated = cur.fetchone()
            if cur.rowcount != 1 or not updated:
                raise VerificationConflict(
                    f"[DB] Could not verify building {building_id}"
                )

            snapshot = dict(building)
            snapshot["verified"] = True
            snapshot["verified_at"] = updated["verified_at"]
            return snapshot
    except VerificationConflict:
        raise
    except Exception as error:
        raise VerificationConflict(
            "Interest verification could not be saved"
        ) from error


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
