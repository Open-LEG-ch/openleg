# SPDX-License-Identifier: AGPL-3.0-or-later
"""Open LEG registry repository.

Repository module for the LEG registry domain: the ``leg_registry`` table
holds self-submitted, human-moderated listings of Swiss Lokale
Elektrizitätsgemeinschaften, independent of which platform (if any) formed
them. See ``docs/leg-registry.md`` for the product contract, in particular
the honesty boundary: a published entry is a moderated self-report, never a
verified grid-topology eligibility signal. The connection seam is resolved
via ``database.get_connection`` at call time so existing tests that
``monkeypatch.setattr(database, "get_connection", ...)`` keep working
unchanged and ``database`` can re-export these functions for legacy callers.
"""

import logging
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)


def _get_connection():
    import database

    return database.get_connection()


# === Registry Entry Operations ===


def save_registry_entry(
    slug: str,
    name: str,
    contact_email: str,
    kanton: str = "",
    plz: str = "",
    ort: str = "",
    bfs_number: Optional[int] = None,
    vnb_name: str = "",
    member_count_estimate: Optional[int] = None,
    leg_status: str = "planung",
    description: str = "",
    website_url: str = "",
    source: str = "self_submitted",
) -> Optional[Dict]:
    """Create a new registry entry, pending moderation. Returns the row id."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO leg_registry (
                        slug, name, contact_email, kanton, plz, ort,
                        bfs_number, vnb_name, member_count_estimate,
                        leg_status, description, website_url,
                        moderation_status, source
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s
                    )
                    RETURNING id
                """,
                    (
                        slug,
                        name,
                        contact_email,
                        kanton,
                        plz,
                        ort,
                        bfs_number,
                        vnb_name,
                        member_count_estimate,
                        leg_status,
                        description,
                        website_url,
                        "pending",
                        source,
                    ),
                )
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        logger.error(f"[DB] Error saving registry entry: {e}")
        return None


def get_registry_entry(entry_id: int) -> Optional[Dict]:
    """Get a registry entry by id, regardless of moderation status."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM leg_registry WHERE id = %s", (entry_id,))
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        logger.error(f"[DB] Error getting registry entry: {e}")
        return None


def get_registry_entry_by_slug(slug: str) -> Optional[Dict]:
    """Get a registry entry by slug, regardless of moderation status.

    Callers that serve public pages must filter on moderation_status
    themselves; this helper does not apply that filter.
    """
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM leg_registry WHERE slug = %s", (slug,))
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        logger.error(f"[DB] Error getting registry entry by slug: {e}")
        return None


def list_registry_entries(
    kanton: Optional[str] = None,
    plz: Optional[str] = None,
    leg_status: Optional[str] = None,
    q: Optional[str] = None,
    moderation_status: str = "published",
) -> List[Dict]:
    """List registry entries, defaulting to published-only.

    Every caller that serves a public page must rely on this default (or
    pass moderation_status='published' explicitly) rather than trust a
    client-supplied moderation status.
    """
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                clauses = ["moderation_status = %s"]
                params: List = [moderation_status]
                if kanton:
                    clauses.append("kanton = %s")
                    params.append(kanton)
                if plz:
                    clauses.append("plz = %s")
                    params.append(plz)
                if leg_status:
                    clauses.append("leg_status = %s")
                    params.append(leg_status)
                if q:
                    clauses.append("(name ILIKE %s OR ort ILIKE %s)")
                    params.extend([f"%{q}%", f"%{q}%"])
                where = " AND ".join(clauses)
                cur.execute(
                    f"""
                    SELECT * FROM leg_registry
                    WHERE {where}
                    ORDER BY created_at DESC
                """,
                    tuple(params),
                )
                return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error listing registry entries: {e}")
        return []


def update_registry_entry_moderation(
    entry_id: int, moderation_status: str, moderation_note: str = ""
) -> bool:
    """Approve or reject a pending registry entry."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE leg_registry
                    SET moderation_status = %s, moderation_note = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """,
                    (moderation_status, moderation_note, entry_id),
                )
                return cur.rowcount > 0
    except Exception as e:
        logger.error(f"[DB] Error updating registry entry moderation: {e}")
        return False


def get_registry_pending_count() -> int:
    """Count entries awaiting moderation."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) as count FROM leg_registry WHERE moderation_status = 'pending'"
                )
                row = cur.fetchone()
                return dict(row)["count"] if row else 0
    except Exception as e:
        logger.error(f"[DB] Error counting pending registry entries: {e}")
        return 0


def set_registry_claim_token(
    entry_id: int, token: str, ttl_seconds: int = 86400
) -> bool:
    """Set a claim-verification token for a registry entry."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE leg_registry
                    SET claim_token = %s,
                        claim_token_expires_at = CURRENT_TIMESTAMP + INTERVAL '%s seconds',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """,
                    (token, ttl_seconds, entry_id),
                )
                return cur.rowcount > 0
    except Exception as e:
        logger.error(f"[DB] Error setting registry claim token: {e}")
        return False


def get_registry_entry_by_claim_token(token: str) -> Optional[Dict]:
    """Get a registry entry by claim token (only if not expired)."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM leg_registry
                    WHERE claim_token = %s AND claim_token_expires_at > CURRENT_TIMESTAMP
                """,
                    (token,),
                )
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        logger.error(f"[DB] Error getting registry entry by claim token: {e}")
        return None


def mark_registry_entry_claimed(entry_id: int, claimed_by_email: str) -> bool:
    """Mark a registry entry as claimed and clear its claim token."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE leg_registry
                    SET source = 'claimed', claimed_at = CURRENT_TIMESTAMP,
                        claimed_by_email = %s, claim_token = NULL,
                        claim_token_expires_at = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """,
                    (claimed_by_email, entry_id),
                )
                return cur.rowcount > 0
    except Exception as e:
        logger.error(f"[DB] Error marking registry entry claimed: {e}")
        return False
