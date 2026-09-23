# SPDX-License-Identifier: AGPL-3.0-or-later
"""Interest intake, verification, retention and municipality aggregates."""

import json
import logging

logger = logging.getLogger(__name__)

UNVERIFIED_INTEREST_RETENTION_DAYS = 30
VERIFIED_COVERAGE_RETENTION_MONTHS = 12


def _get_connection():
    import database

    return database.get_connection()


def save_coverage_request(
    *,
    request_id,
    email,
    address,
    plz,
    municipality_name,
    canton,
    bfs_number,
    roles,
    has_solar,
    verification_token,
):
    """Persist an address-coverage request pending email verification."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO coverage_requests (
                    request_id, email, address, plz, municipality_name, canton,
                    bfs_number, roles, has_solar, verification_token,
                    token_expires_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    CURRENT_TIMESTAMP + INTERVAL '30 days')
                """,
                (
                    request_id,
                    email,
                    address,
                    plz,
                    municipality_name,
                    canton,
                    bfs_number,
                    json.dumps(roles),
                    has_solar,
                    verification_token,
                ),
            )
            return True
    except Exception:
        logger.exception("[DB] Error saving coverage request")
        return False


def verify_coverage_request(token):
    """Verify one current coverage request without returning personal data."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE coverage_requests
                SET verified = TRUE,
                    verified_at = CURRENT_TIMESTAMP,
                    verification_token = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE verification_token = %s
                  AND verified = FALSE
                  AND token_expires_at > CURRENT_TIMESTAMP
                RETURNING email, bfs_number, municipality_name
                """,
                (token,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception:
        logger.exception("[DB] Error verifying coverage request")
        return None


def cleanup_expired_interest():
    """Delete stale unverified and year-old unresolved interest records."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                DELETE FROM coverage_requests
                WHERE (verified = FALSE AND created_at < CURRENT_TIMESTAMP - INTERVAL '{UNVERIFIED_INTEREST_RETENTION_DAYS} days')
                   OR (verified = TRUE AND verified_at < CURRENT_TIMESTAMP - INTERVAL '{VERIFIED_COVERAGE_RETENTION_MONTHS} months')
                """
            )
            coverage_deleted = cur.rowcount
            cur.execute(
                f"""
                DELETE FROM buildings
                WHERE verified = FALSE
                  AND COALESCE(verification_requested_at, registered_at)
                      < CURRENT_TIMESTAMP - INTERVAL '{UNVERIFIED_INTEREST_RETENTION_DAYS} days'
                  AND NOT EXISTS (SELECT 1 FROM consents WHERE consents.building_id = buildings.building_id)
                  AND NOT EXISTS (SELECT 1 FROM referrals WHERE referrals.referrer_id = buildings.building_id OR referrals.referred_id = buildings.building_id)
                  AND NOT EXISTS (SELECT 1 FROM community_members WHERE community_members.building_id = buildings.building_id)
                  AND NOT EXISTS (SELECT 1 FROM communities WHERE communities.admin_building_id = buildings.building_id)
                """
            )
            return {
                "coverage_requests_deleted": coverage_deleted,
                "buildings_deleted": cur.rowcount,
            }
    except Exception:
        logger.exception("[DB] Error cleaning expired interest")
        return None


def get_operator_interest_records(limit=500):
    """Return both intake paths for the private operator dashboard."""
    try:
        limit = max(1, min(int(limit), 2000))
    except (TypeError, ValueError):
        limit = 500
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT 'address_check' AS source, email, address, plz,
                       municipality_name, canton, bfs_number, roles, has_solar,
                       verified, registered_at AS created_at
                FROM buildings
                UNION ALL
                SELECT 'coverage_request' AS source, email, address, plz,
                       municipality_name, canton, bfs_number, roles, has_solar,
                       verified, created_at
                FROM coverage_requests
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]
    except Exception:
        logger.exception("[DB] Error loading operator interest records")
        return []


def get_operator_interest_counts():
    """Count both raw intake sources independently of the displayed row limit."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FILTER (WHERE verified IS TRUE) AS interest_verified,
                       COUNT(*) FILTER (WHERE verified IS NOT TRUE) AS interest_unverified
                FROM (
                    SELECT verified FROM buildings
                    UNION ALL
                    SELECT verified FROM coverage_requests
                ) interest
            """)
            row = cur.fetchone()
            return {
                "interest_verified": int(row["interest_verified"]),
                "interest_unverified": int(row["interest_unverified"]),
            }
    except Exception:
        logger.exception("[DB] Error counting operator interest records")
        return {"interest_verified": None, "interest_unverified": None}


def get_interest_counts_by_bfs():
    """Return exact verified household totals keyed by BFS municipality."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT bfs_number, COUNT(*) AS interest_count
                FROM verified_interest
                GROUP BY bfs_number
                """
            )
            return {
                int(row["bfs_number"]): int(row["interest_count"])
                for row in cur.fetchall()
            }
    except Exception:
        logger.exception("[DB] Error loading municipality interest counts")
        return {}


def get_interest_count(bfs_number):
    """Return a municipality total, or None for an absent/failed lookup.

    The distinction preserves the profile's zero and notification's one
    fallbacks without querying the national aggregate.
    """
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS interest_count FROM verified_interest
                WHERE bfs_number = %s GROUP BY bfs_number
                """,
                (bfs_number,),
            )
            row = cur.fetchone()
            return int(row["interest_count"]) if row else None
    except Exception:
        logger.exception("[DB] Error loading municipality interest count")
        return None


def get_verified_interest_recipients(bfs_number, exclude_email=""):
    """Return distinct verified recipient addresses for one municipality."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT v.email
                FROM verified_interest v
                LEFT JOIN consents c
                    ON c.building_id = v.source_id
                    AND v.address_problem = FALSE
                WHERE v.bfs_number = %s
                  AND v.email <> LOWER(%s)
                  AND (v.address_problem = TRUE OR c.updates_opt_in IS TRUE)
                ORDER BY v.email
                """,
                (bfs_number, exclude_email),
            )
            return [row["email"] for row in cur.fetchall() if row.get("email")]
    except Exception:
        logger.exception("[DB] Error loading verified interest recipients")
        return []


def get_municipality_interest_summary(bfs_number):
    """Return aggregate-only demand facts for a municipality dashboard."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) AS verified_total,
                    COUNT(*) FILTER (
                        WHERE created_at >= CURRENT_TIMESTAMP - INTERVAL '30 days'
                    ) AS last_30_days,
                    COUNT(*) FILTER (WHERE has_solar = TRUE) AS has_solar,
                    COUNT(*) FILTER (WHERE address_problem = TRUE) AS address_problems
                FROM verified_interest WHERE bfs_number = %s
                """,
                (bfs_number,),
            )
            summary = dict(cur.fetchone() or {})
            cur.execute(
                """
                SELECT role, COUNT(*) AS count
                FROM verified_interest,
                     LATERAL jsonb_array_elements_text(roles) AS expanded(role)
                WHERE bfs_number = %s
                GROUP BY role ORDER BY role
                """,
                (bfs_number,),
            )
            summary["roles"] = {
                row["role"]: int(row["count"]) for row in cur.fetchall()
            }
            return summary
    except Exception:
        logger.exception("[DB] Error loading municipality interest summary")
        return {}
