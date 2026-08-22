# SPDX-License-Identifier: AGPL-3.0-or-later
"""Building registration repository.

Owns building records, consent-gated building reads, and dashboard building data.
"""

import logging
import time

logger = logging.getLogger(__name__)


def _get_connection():
    import database

    return database.get_connection()


def save_building(
    building_id: str,
    email: str,
    profile: dict,
    consents: dict,
    user_type: str = "anonymous",
    phone: str | None = None,
    referrer_id: str | None = None,
    city_id: str | None = None,
) -> bool:
    """Save or update a building record."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            # Generate unique referral code
            import secrets

            referral_code = secrets.token_urlsafe(8)

            cur.execute(
                """
                    INSERT INTO buildings (
                        building_id, email, phone, address, lat, lon, plz,
                        building_type, annual_consumption_kwh, potential_pv_kwp,
                        registered_at, verified, verified_at, user_type,
                        referrer_id, referral_code, city_id
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        to_timestamp(%s), %s, to_timestamp(%s), %s, %s, %s, %s
                    )
                    ON CONFLICT (building_id) DO UPDATE SET
                        email = EXCLUDED.email,
                        phone = EXCLUDED.phone,
                        verified = EXCLUDED.verified,
                        verified_at = EXCLUDED.verified_at,
                        user_type = EXCLUDED.user_type,
                        updated_at = CURRENT_TIMESTAMP
                """,
                (
                    building_id,
                    email,
                    phone or "",
                    profile.get("address", ""),
                    profile.get("lat"),
                    profile.get("lon"),
                    profile.get("plz"),
                    profile.get("building_type"),
                    profile.get("annual_consumption_kwh"),
                    profile.get("potential_pv_kwp"),
                    time.time(),
                    True,  # verified immediately for now
                    time.time(),
                    user_type,
                    referrer_id or "",
                    referral_code,
                    city_id or "baden",
                ),
            )

            # Save consents
            cur.execute(
                """
                    INSERT INTO consents (
                        building_id, share_with_neighbors, share_with_utility,
                        updates_opt_in, consent_version, consent_timestamp
                    ) VALUES (%s, %s, %s, %s, %s, to_timestamp(%s))
                    ON CONFLICT (building_id) DO UPDATE SET
                        share_with_neighbors = EXCLUDED.share_with_neighbors,
                        share_with_utility = EXCLUDED.share_with_utility,
                        updates_opt_in = EXCLUDED.updates_opt_in,
                        consent_version = EXCLUDED.consent_version,
                        consent_timestamp = EXCLUDED.consent_timestamp
                """,
                (
                    building_id,
                    consents.get("share_with_neighbors", False),
                    consents.get("share_with_utility", False),
                    consents.get("updates_opt_in", False),
                    consents.get("consent_version", "1.0"),
                    consents.get("consent_timestamp", time.time()),
                ),
            )

            # Track referral if present
            if referrer_id:
                cur.execute(
                    """
                        INSERT INTO referrals (referrer_id, referred_id)
                        VALUES (%s, %s)
                        ON CONFLICT (referred_id) DO NOTHING
                    """,
                    (referrer_id, building_id),
                )

            return True
    except Exception as e:
        logger.error(f"[DB] Error saving building {building_id}: {e}")
        return False


def get_building(building_id: str) -> dict | None:
    """Get a building record by ID."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT b.*, c.share_with_neighbors, c.share_with_utility,
                           c.updates_opt_in, c.consent_version
                    FROM buildings b
                    LEFT JOIN consents c ON b.building_id = c.building_id
                    WHERE b.building_id = %s
                """,
                (building_id,),
            )
            row = cur.fetchone()
            if row:
                return dict(row)
            return None
    except Exception as e:
        logger.error(f"[DB] Error getting building {building_id}: {e}")
        return None


def get_building_by_email(email: str) -> list[dict]:
    """Find buildings by email address."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT building_id FROM buildings
                    WHERE LOWER(email) = LOWER(%s)
                """,
                (email,),
            )
            return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error finding buildings by email: {e}")
        return []


def get_all_buildings(city_id: str | None = None) -> list[dict]:
    """Get all buildings for map display, optionally scoped by city_id."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            if city_id:
                cur.execute(
                    """
                        SELECT b.building_id, b.lat, b.lon, b.user_type, b.verified
                        FROM buildings b
                        INNER JOIN consents c ON b.building_id = c.building_id
                        WHERE b.verified = TRUE
                        AND c.share_with_neighbors = TRUE AND b.city_id = %s
                    """,
                    (city_id,),
                )
            else:
                cur.execute("""
                        SELECT b.building_id, b.lat, b.lon, b.user_type, b.verified
                        FROM buildings b
                        INNER JOIN consents c ON b.building_id = c.building_id
                        WHERE b.verified = TRUE
                        AND c.share_with_neighbors = TRUE
                    """)
            return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error getting all buildings: {e}")
        return []


def get_all_building_profiles(city_id: str | None = None) -> list[dict]:
    """Get all building profiles for ML clustering, optionally scoped by city_id."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            if city_id:
                cur.execute(
                    """
                        SELECT b.building_id, b.address, b.lat, b.lon, b.plz, b.building_type,
                               b.annual_consumption_kwh, b.potential_pv_kwp, b.user_type
                        FROM buildings b
                        INNER JOIN consents c ON b.building_id = c.building_id
                        AND c.share_with_neighbors = TRUE
                        WHERE b.verified = TRUE
                          AND b.city_id = %s
                    """,
                    (city_id,),
                )
            else:
                cur.execute("""
                        SELECT b.building_id, b.address, b.lat, b.lon, b.plz, b.building_type,
                               b.annual_consumption_kwh, b.potential_pv_kwp, b.user_type
                        FROM buildings b
                        INNER JOIN consents c ON b.building_id = c.building_id
                        AND c.share_with_neighbors = TRUE
                        WHERE b.verified = TRUE
                    """)
            return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error getting building profiles: {e}")
        return []


def get_operator_building_profiles(city_id: str | None = None) -> list[dict]:
    """Operator-only read of all building profiles. Must never feed a resident-visible response."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            if city_id:
                cur.execute(
                    """
                        SELECT building_id, address, lat, lon, plz, building_type,
                               annual_consumption_kwh, potential_pv_kwp, user_type
                        FROM buildings
                        WHERE verified = TRUE AND city_id = %s
                    """,
                    (city_id,),
                )
            else:
                cur.execute("""
                        SELECT building_id, address, lat, lon, plz, building_type,
                               annual_consumption_kwh, potential_pv_kwp, user_type
                        FROM buildings
                        WHERE verified = TRUE
                    """)
            return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error getting operator building profiles: {e}")
        return []


def delete_building(building_id: str) -> bool:
    """Delete a building and all related records."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM buildings WHERE building_id = %s", (building_id,))
            return cur.rowcount > 0
    except Exception as e:
        logger.error(f"[DB] Error deleting building {building_id}: {e}")
        return False


def update_building_verified(building_id: str, verified: bool = True) -> bool:
    """Update building verification status."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE buildings
                    SET verified = %s, verified_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                    WHERE building_id = %s
                """,
                    (verified, building_id),
                )
                return cur.rowcount > 0
    except Exception as e:
        logger.error(f"[DB] Error updating verification for {building_id}: {e}")
        return False


NEIGHBOR_BOX_HALF_WIDTH_KM = 0.5


def get_neighbor_count_near(
    lat: float,
    lon: float,
    box_half_width_km: float = NEIGHBOR_BOX_HALF_WIDTH_KM,
    city_id: str | None = None,
) -> int:
    """Count visible buildings in a square around a point, optionally by city.

    ``box_half_width_km`` is the approximate distance from the centre to each
    side of the latitude/longitude-aligned bounding box.
    """
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            lat_offset = box_half_width_km / 111.0
            lon_offset = box_half_width_km / (111.0 * 0.7)  # rough cos(47)
            if city_id:
                cur.execute(
                    """
                        SELECT COUNT(*) as count FROM buildings b
                        INNER JOIN consents c ON b.building_id = c.building_id
                        WHERE b.verified = TRUE
                        AND c.share_with_neighbors = TRUE AND b.city_id = %s
                        AND b.lat BETWEEN %s AND %s
                        AND b.lon BETWEEN %s AND %s
                    """,
                    (
                        city_id,
                        lat - lat_offset,
                        lat + lat_offset,
                        lon - lon_offset,
                        lon + lon_offset,
                    ),
                )
            else:
                cur.execute(
                    """
                        SELECT COUNT(*) as count FROM buildings b
                        INNER JOIN consents c ON b.building_id = c.building_id
                        WHERE b.verified = TRUE
                        AND c.share_with_neighbors = TRUE
                        AND b.lat BETWEEN %s AND %s
                        AND b.lon BETWEEN %s AND %s
                    """,
                    (
                        lat - lat_offset,
                        lat + lat_offset,
                        lon - lon_offset,
                        lon + lon_offset,
                    ),
                )
            row = cur.fetchone()
            return row["count"] if row else 0
    except Exception as e:
        logger.error(f"[DB] Error counting neighbors: {e}")
        return 0


def get_building_for_dashboard(building_id: str) -> dict | None:
    """Get full building data for dashboard display."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT b.*, c.share_with_neighbors, c.share_with_utility,
                           c.updates_opt_in, c.consent_version,
                           (SELECT COUNT(*) FROM referrals WHERE referrer_id = b.building_id) as referral_count,
                           (SELECT COUNT(*) FROM community_members WHERE building_id = b.building_id AND status = 'confirmed') as community_count
                    FROM buildings b
                    LEFT JOIN consents c ON b.building_id = c.building_id
                    WHERE b.building_id = %s
                """,
                    (building_id,),
                )
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        logger.error(f"[DB] Error getting dashboard data: {e}")
        return None
