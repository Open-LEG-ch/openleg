# SPDX-License-Identifier: AGPL-3.0-or-later
"""API client identity and usage persistence."""

import json
import logging

logger = logging.getLogger(__name__)


def _get_connection():
    import database

    return database.get_connection()


def save_api_client(
    company_name,
    contact_email,
    api_key_hash,
    tier="starter",
    rate_limit=100,
    allowed_cantons=None,
):
    """Save an API client and return its database identifier."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    INSERT INTO api_clients (
                        company_name, contact_email, api_key_hash, tier,
                        rate_limit_per_hour, allowed_cantons
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                """,
                (
                    company_name,
                    contact_email,
                    api_key_hash,
                    tier,
                    rate_limit,
                    json.dumps(allowed_cantons or ["ZH"]),
                ),
            )
            row = cur.fetchone()
            return row["id"] if row else None
    except Exception as exc:
        logger.error("[DB] Error saving API client: %s", exc)
        return None


def get_api_client_by_key(api_key_hash):
    """Return the active API client matching a hashed key."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM api_clients WHERE api_key_hash = %s AND active = TRUE",
                (api_key_hash,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as exc:
        logger.error("[DB] Error getting API client: %s", exc)
        return None


def track_api_usage(client_id, endpoint, params=None, response_size=0):
    """Record one API call for an authenticated client."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    INSERT INTO api_usage (client_id, endpoint, params, response_size)
                    VALUES (%s, %s, %s, %s)
                """,
                (client_id, endpoint, json.dumps(params or {}), response_size),
            )
            return True
    except Exception as exc:
        logger.error("[DB] Error tracking API usage: %s", exc)
        return False


def get_api_usage_count(client_id, hours=1):
    """Count client calls in a positive whole-hour window."""
    if isinstance(hours, bool) or not isinstance(hours, int) or hours <= 0:
        return 0

    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT COUNT(*) as count FROM api_usage
                    WHERE client_id = %s
                      AND called_at > CURRENT_TIMESTAMP - INTERVAL '1 hour' * %s
                """,
                (client_id, hours),
            )
            row = cur.fetchone()
            return row["count"] if row else 0
    except Exception as exc:
        logger.error("[DB] Error getting API usage count: %s", exc)
        return 0
