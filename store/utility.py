# SPDX-License-Identifier: AGPL-3.0-or-later
"""EVU/VNB utility-client repository.

Repository module for the EVU/VNB utility-client domain: the ``utility_clients``
table holds client accounts, magic-link tokens, status, API keys and stats.
The connection seam is resolved via ``database.get_connection`` at call time
so existing tests that ``monkeypatch.setattr(database, "get_connection", ...)``
keep working unchanged and ``database`` can re-export these functions for
legacy callers.
"""

import logging
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)


def _get_connection():
    import database

    return database.get_connection()


# === Utility Client Operations ===


def save_utility_client(
    client_id: str,
    company_name: str,
    contact_email: str,
    contact_name: str = "",
    contact_phone: str = "",
    vnb_name: str = "",
    population: Optional[int] = None,
    kanton: str = "",
    tier: str = "starter",
) -> bool:
    """Create or update a utility client."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO utility_clients (
                        client_id, company_name, contact_name, contact_email,
                        contact_phone, vnb_name, population, kanton, tier, status
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')
                    ON CONFLICT (client_id) DO UPDATE SET
                        company_name = EXCLUDED.company_name,
                        contact_name = EXCLUDED.contact_name,
                        contact_email = EXCLUDED.contact_email,
                        contact_phone = EXCLUDED.contact_phone,
                        vnb_name = EXCLUDED.vnb_name,
                        population = EXCLUDED.population,
                        kanton = EXCLUDED.kanton,
                        tier = EXCLUDED.tier,
                        updated_at = CURRENT_TIMESTAMP
                """,
                    (
                        client_id,
                        company_name,
                        contact_name,
                        contact_email,
                        contact_phone,
                        vnb_name,
                        population,
                        kanton,
                        tier,
                    ),
                )
                return True
    except Exception as e:
        logger.error(f"[DB] Error saving utility client: {e}")
        return False


def get_utility_client(client_id: str) -> Optional[Dict]:
    """Get a utility client by client_id."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM utility_clients WHERE client_id = %s", (client_id,)
                )
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        logger.error(f"[DB] Error getting utility client: {e}")
        return None


def get_utility_client_by_email(email: str) -> Optional[Dict]:
    """Get a utility client by contact email."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM utility_clients WHERE LOWER(contact_email) = LOWER(%s)",
                    (email,),
                )
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        logger.error(f"[DB] Error getting utility client by email: {e}")
        return None


def get_utility_client_by_magic_token(token: str) -> Optional[Dict]:
    """Get utility client by magic link token (only if not expired)."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM utility_clients
                    WHERE magic_link_token = %s AND magic_link_expires_at > CURRENT_TIMESTAMP
                """,
                    (token,),
                )
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        logger.error(f"[DB] Error getting utility client by magic token: {e}")
        return None


def set_utility_magic_token(client_id: str, token: str, ttl_seconds: int = 900) -> bool:
    """Set a magic link token for a utility client (default 15min TTL)."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE utility_clients
                    SET magic_link_token = %s,
                        magic_link_expires_at = CURRENT_TIMESTAMP + INTERVAL '%s seconds',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE client_id = %s
                """,
                    (token, ttl_seconds, client_id),
                )
                return cur.rowcount > 0
    except Exception as e:
        logger.error(f"[DB] Error setting magic token: {e}")
        return False


def clear_utility_magic_token(client_id: str) -> bool:
    """Clear magic link token after use and update last_login_at."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE utility_clients
                    SET magic_link_token = NULL, magic_link_expires_at = NULL,
                        last_login_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                    WHERE client_id = %s
                """,
                    (client_id,),
                )
                return cur.rowcount > 0
    except Exception as e:
        logger.error(f"[DB] Error clearing magic token: {e}")
        return False


def update_utility_client_status(client_id: str, status: str) -> bool:
    """Update utility client status."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE utility_clients SET status = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE client_id = %s
                """,
                    (status, client_id),
                )
                return cur.rowcount > 0
    except Exception as e:
        logger.error(f"[DB] Error updating utility client status: {e}")
        return False


def update_utility_client_api_key(client_id: str, api_key_hash: str) -> bool:
    """Set API key hash for a utility client."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE utility_clients SET api_key_hash = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE client_id = %s
                """,
                    (api_key_hash, client_id),
                )
                return cur.rowcount > 0
    except Exception as e:
        logger.error(f"[DB] Error updating utility client API key: {e}")
        return False


def get_all_utility_clients(status: Optional[str] = None) -> List[Dict]:
    """Get all utility clients, optionally filtered by status."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                if status:
                    cur.execute(
                        "SELECT * FROM utility_clients WHERE status = %s ORDER BY created_at DESC",
                        (status,),
                    )
                else:
                    cur.execute(
                        "SELECT * FROM utility_clients ORDER BY created_at DESC"
                    )
                return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error getting utility clients: {e}")
        return []


def get_utility_client_stats() -> Dict:
    """Get utility client statistics."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        COUNT(*) as total,
                        COUNT(*) FILTER (WHERE status = 'active') as active,
                        COUNT(*) FILTER (WHERE status = 'pending') as pending,
                        COUNT(*) FILTER (WHERE status = 'trial') as trial,
                        COUNT(*) FILTER (WHERE tier = 'starter') as tier_starter,
                        COUNT(*) FILTER (WHERE tier = 'professional') as tier_professional,
                        COUNT(*) FILTER (WHERE tier = 'enterprise') as tier_enterprise
                    FROM utility_clients
                """)
                return dict(cur.fetchone())
    except Exception as e:
        logger.error(f"[DB] Error getting utility client stats: {e}")
        return {}
