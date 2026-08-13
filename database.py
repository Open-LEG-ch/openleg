# SPDX-License-Identifier: AGPL-3.0-or-later
"""
PostgreSQL Database Layer for OpenLEG
Replaces JSON file persistence with proper database storage.
"""

import json
import logging
import os
import time
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# Check for psycopg2
try:
    from psycopg2 import pool  # type: ignore
    from psycopg2.extras import RealDictCursor  # type: ignore

    HAS_POSTGRES = True
except ImportError:
    HAS_POSTGRES = False
    logger.warning("[DB] psycopg2 not installed, PostgreSQL features disabled")

# Database configuration
DATABASE_URL = os.getenv("DATABASE_URL", "")
DB_POOL_MIN = int(os.getenv("DB_POOL_MIN", "2"))
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))

# Connection pool
_connection_pool = None


def init_db():
    """Initialize database connection pool and create tables if needed."""
    global _connection_pool

    if not HAS_POSTGRES:
        logger.warning("[DB] PostgreSQL not available, using fallback JSON storage")
        return False

    if not DATABASE_URL:
        logger.warning("[DB] DATABASE_URL not set, using fallback JSON storage")
        return False

    try:
        _connection_pool = pool.ThreadedConnectionPool(
            DB_POOL_MIN, DB_POOL_MAX, DATABASE_URL, cursor_factory=RealDictCursor
        )
        logger.info(
            f"[DB] Connection pool created (min={DB_POOL_MIN}, max={DB_POOL_MAX})"
        )

        # Create tables
        _create_tables()
        return True
    except Exception as e:
        logger.error(f"[DB] Failed to initialize database: {e}")
        return False


@contextmanager
def get_connection():
    """Get a database connection from the pool."""
    conn = None
    try:
        conn = _connection_pool.getconn()
        yield conn
        conn.commit()
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            _connection_pool.putconn(conn)


def _create_tables():
    """Create database tables if they don't exist."""
    create_tables()


# === Building Operations ===


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
        with get_connection() as conn, conn.cursor() as cur:
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
                        to_timestamp(%s), %s, %s, %s, %s, %s, %s
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
        with get_connection() as conn, conn.cursor() as cur:
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
        with get_connection() as conn, conn.cursor() as cur:
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
        with get_connection() as conn, conn.cursor() as cur:
            if city_id:
                cur.execute(
                    """
                        SELECT building_id, lat, lon, user_type, verified
                        FROM buildings
                        WHERE verified = TRUE AND city_id = %s
                    """,
                    (city_id,),
                )
            else:
                cur.execute("""
                        SELECT building_id, lat, lon, user_type, verified
                        FROM buildings
                        WHERE verified = TRUE
                    """)
            return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error getting all buildings: {e}")
        return []


def get_all_building_profiles(city_id: str | None = None) -> list[dict]:
    """Get all building profiles for ML clustering, optionally scoped by city_id."""
    try:
        with get_connection() as conn, conn.cursor() as cur:
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
        logger.error(f"[DB] Error getting building profiles: {e}")
        return []


def delete_building(building_id: str) -> bool:
    """Delete a building and all related records."""
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM buildings WHERE building_id = %s", (building_id,))
            return cur.rowcount > 0
    except Exception as e:
        logger.error(f"[DB] Error deleting building {building_id}: {e}")
        return False


def update_building_verified(building_id: str, verified: bool = True) -> bool:
    """Update building verification status."""
    try:
        with get_connection() as conn:
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


# === Cluster Operations ===


def save_cluster(building_id: str, cluster_id: int) -> bool:
    """Save cluster assignment for a building."""
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    INSERT INTO clusters (building_id, cluster_id)
                    VALUES (%s, %s)
                    ON CONFLICT (building_id) DO UPDATE SET
                        cluster_id = EXCLUDED.cluster_id,
                        updated_at = CURRENT_TIMESTAMP
                """,
                (building_id, cluster_id),
            )
            return True
    except Exception as e:
        logger.error(f"[DB] Error saving cluster: {e}")
        return False


def save_cluster_info(cluster_id: int, info: dict) -> bool:
    """Save cluster metadata."""
    try:
        import json

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO cluster_info (cluster_id, autarky_percent, num_members, polygon)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (cluster_id) DO UPDATE SET
                        autarky_percent = EXCLUDED.autarky_percent,
                        num_members = EXCLUDED.num_members,
                        polygon = EXCLUDED.polygon,
                        updated_at = CURRENT_TIMESTAMP
                """,
                    (
                        cluster_id,
                        info.get("autarky_percent"),
                        info.get("num_members"),
                        json.dumps(info.get("polygon", [])),
                    ),
                )
                return True
    except Exception as e:
        logger.error(f"[DB] Error saving cluster info: {e}")
        return False


# === Referral Operations ===


def get_referral_code(building_id: str) -> str | None:
    """Get the referral code for a building."""
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT referral_code FROM buildings WHERE building_id = %s
                """,
                (building_id,),
            )
            row = cur.fetchone()
            if row:
                return row["referral_code"]
            return None
    except Exception as e:
        logger.error(f"[DB] Error getting referral code: {e}")
        return None


def get_building_by_referral_code(code: str) -> dict | None:
    """Find a building by its referral code."""
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT building_id, email, address FROM buildings
                    WHERE referral_code = %s
                """,
                (code,),
            )
            row = cur.fetchone()
            if row:
                return dict(row)
            return None
    except Exception as e:
        logger.error(f"[DB] Error finding building by referral code: {e}")
        return None


def get_referral_stats(building_id: str) -> dict:
    """Get referral statistics for a building."""
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT COUNT(*) as total_referrals
                    FROM referrals WHERE referrer_id = %s
                """,
                (building_id,),
            )
            row = cur.fetchone()
            return {"total_referrals": row["total_referrals"] if row else 0}
    except Exception as e:
        logger.error(f"[DB] Error getting referral stats: {e}")
        return {"total_referrals": 0}


def get_referral_leaderboard(limit: int = 10, city_id: str | None = None) -> list[dict]:
    """Get top referrers, optionally scoped by city_id."""
    try:
        with get_connection() as conn, conn.cursor() as cur:
            if city_id:
                cur.execute(
                    """
                        SELECT b.building_id,
                               SPLIT_PART(b.address, ',', 1) as street,
                               COUNT(r.id) as referral_count
                        FROM buildings b
                        JOIN referrals r ON b.building_id = r.referrer_id
                        WHERE b.city_id = %s
                        GROUP BY b.building_id, b.address
                        ORDER BY referral_count DESC
                        LIMIT %s
                    """,
                    (city_id, limit),
                )
            else:
                cur.execute(
                    """
                        SELECT b.building_id,
                               SPLIT_PART(b.address, ',', 1) as street,
                               COUNT(r.id) as referral_count
                        FROM buildings b
                        JOIN referrals r ON b.building_id = r.referrer_id
                        GROUP BY b.building_id, b.address
                        ORDER BY referral_count DESC
                        LIMIT %s
                    """,
                    (limit,),
                )
            return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error getting leaderboard: {e}")
        return []


# === Analytics Operations ===


def track_event(
    event_type: str, building_id: str | None = None, data: dict | None = None
) -> bool:
    """Track an analytics event."""
    try:
        import json

        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    INSERT INTO analytics_events (event_type, building_id, data)
                    VALUES (%s, %s, %s)
                """,
                (
                    event_type,
                    building_id or "",
                    json.dumps(data if data is not None else {}),
                ),
            )
            return True
    except Exception as e:
        logger.error(f"[DB] Error tracking event: {e}")
        return False


def get_stats(city_id: str | None = None) -> dict:
    """Get platform statistics, optionally scoped by city_id."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                stats = {}
                city_filter = " AND city_id = %s" if city_id else ""
                city_params = (city_id,) if city_id else ()

                # Total buildings
                cur.execute(
                    f"SELECT COUNT(*) as count FROM buildings WHERE verified = TRUE{city_filter}",
                    city_params,
                )
                stats["total_buildings"] = cur.fetchone()["count"]

                # By type
                cur.execute(
                    f"""
                    SELECT user_type, COUNT(*) as count
                    FROM buildings WHERE verified = TRUE{city_filter}
                    GROUP BY user_type
                """,
                    city_params,
                )
                for row in cur.fetchall():
                    stats[f"{row['user_type']}_count"] = row["count"]

                # Total referrals
                if city_id:
                    cur.execute(
                        """
                        SELECT COUNT(*) as count FROM referrals r
                        JOIN buildings b ON r.referrer_id = b.building_id
                        WHERE b.city_id = %s
                    """,
                        (city_id,),
                    )
                else:
                    cur.execute("SELECT COUNT(*) as count FROM referrals")
                stats["total_referrals"] = cur.fetchone()["count"]

                # Registrations today
                cur.execute(
                    f"""
                    SELECT COUNT(*) as count FROM buildings
                    WHERE DATE(registered_at) = CURRENT_DATE{city_filter}
                """,
                    city_params,
                )
                stats["registrations_today"] = cur.fetchone()["count"]

                return stats
    except Exception as e:
        logger.error(f"[DB] Error getting stats: {e}")
        return {}


# === Migration from JSON ===


def migrate_from_json(json_data: dict) -> tuple[int, int]:
    """
    Migrate data from JSON format to PostgreSQL.
    Returns (success_count, error_count).
    """
    success = 0
    errors = 0

    buildings = json_data.get("buildings", {})
    interest_pool = json_data.get("interest_pool", {})

    # Migrate registered buildings
    for building_id, data in buildings.items():
        try:
            profile = data.get("profile", {})
            consents = data.get("consents", {})

            save_building(
                building_id=building_id,
                email=data.get("email", ""),
                profile=profile,
                consents=consents,
                user_type="registered",
                phone=data.get("phone"),
            )
            success += 1
        except Exception as e:
            logger.error(f"[MIGRATION] Error migrating building {building_id}: {e}")
            errors += 1

    # Migrate interest pool (anonymous)
    for building_id, data in interest_pool.items():
        try:
            profile = data.get("profile", {})
            consents = data.get("consents", {})

            save_building(
                building_id=building_id,
                email=data.get("email", ""),
                profile=profile,
                consents=consents,
                user_type="anonymous",
                phone=data.get("phone"),
            )
            success += 1
        except Exception as e:
            logger.error(f"[MIGRATION] Error migrating interest {building_id}: {e}")
            errors += 1

    logger.info(f"[MIGRATION] Completed: {success} success, {errors} errors")
    return success, errors


def get_neighbor_count_near(
    lat: float, lon: float, radius_km: float = 0.5, city_id: str | None = None
) -> int:
    """Count verified buildings within radius of a point, optionally scoped by city_id."""
    try:
        with get_connection() as conn, conn.cursor() as cur:
            # Approximate degree offset for radius
            lat_offset = radius_km / 111.0
            lon_offset = radius_km / (111.0 * 0.7)  # rough cos(47)
            if city_id:
                cur.execute(
                    """
                        SELECT COUNT(*) as count FROM buildings
                        WHERE verified = TRUE AND city_id = %s
                        AND lat BETWEEN %s AND %s
                        AND lon BETWEEN %s AND %s
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
                        SELECT COUNT(*) as count FROM buildings
                        WHERE verified = TRUE
                        AND lat BETWEEN %s AND %s
                        AND lon BETWEEN %s AND %s
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
        with get_connection() as conn:
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


# === Municipality Operations ===


def save_municipality(
    bfs_number, name, kanton="ZH", dso_name=None, population=None, subdomain=None
):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO municipalities (bfs_number, name, kanton, dso_name, population, subdomain)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (bfs_number) DO UPDATE SET
                        name = EXCLUDED.name, dso_name = EXCLUDED.dso_name,
                        population = EXCLUDED.population, updated_at = CURRENT_TIMESTAMP
                    RETURNING id
                """,
                    (bfs_number, name, kanton, dso_name, population, subdomain),
                )
                row = cur.fetchone()
                return row["id"] if row else None
    except Exception as e:
        logger.error(f"[DB] Error saving municipality: {e}")
        return None


def get_municipality(bfs_number=None, subdomain=None):
    try:
        with get_connection() as conn, conn.cursor() as cur:
            if bfs_number:
                cur.execute(
                    "SELECT * FROM municipalities WHERE bfs_number = %s",
                    (bfs_number,),
                )
            elif subdomain:
                cur.execute(
                    "SELECT * FROM municipalities WHERE subdomain = %s",
                    (subdomain,),
                )
            else:
                return None
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"[DB] Error getting municipality: {e}")
        return None


def get_all_municipalities(kanton=None):
    try:
        with get_connection() as conn, conn.cursor() as cur:
            if kanton:
                cur.execute(
                    "SELECT * FROM municipalities WHERE kanton = %s ORDER BY name",
                    (kanton,),
                )
            else:
                cur.execute("SELECT * FROM municipalities ORDER BY name")
            return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error getting municipalities: {e}")
        return []


def update_municipality_status(bfs_number, status, admin_email=None):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                if admin_email:
                    cur.execute(
                        """
                        UPDATE municipalities SET onboarding_status = %s, admin_email = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE bfs_number = %s
                    """,
                        (status, admin_email, bfs_number),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE municipalities SET onboarding_status = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE bfs_number = %s
                    """,
                        (status, bfs_number),
                    )
                return cur.rowcount > 0
    except Exception as e:
        logger.error(f"[DB] Error updating municipality status: {e}")
        return False


# === Data Consent Operations ===


def save_data_consent(
    building_id,
    tier=1,
    share_municipality=True,
    share_research=False,
    share_providers=False,
    version="1.0",
):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO data_consents (building_id, tier, share_with_municipality, share_anonymized_research,
                        share_aggregated_providers, consent_version)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (building_id) DO UPDATE SET
                        tier = EXCLUDED.tier,
                        share_with_municipality = EXCLUDED.share_with_municipality,
                        share_anonymized_research = EXCLUDED.share_anonymized_research,
                        share_aggregated_providers = EXCLUDED.share_aggregated_providers,
                        consent_version = EXCLUDED.consent_version,
                        consented_at = CURRENT_TIMESTAMP, revoked_at = NULL
                """,
                    (
                        building_id,
                        tier,
                        share_municipality,
                        share_research,
                        share_providers,
                        version,
                    ),
                )
                return True
    except Exception as e:
        logger.error(f"[DB] Error saving data consent: {e}")
        return False


def get_data_consent(building_id):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM data_consents WHERE building_id = %s AND revoked_at IS NULL",
                    (building_id,),
                )
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        logger.error(f"[DB] Error getting data consent: {e}")
        return None


def count_consented_buildings(tier=None):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                if tier:
                    cur.execute(
                        "SELECT COUNT(*) as count FROM data_consents WHERE tier >= %s AND revoked_at IS NULL",
                        (tier,),
                    )
                else:
                    cur.execute(
                        "SELECT COUNT(*) as count FROM data_consents WHERE revoked_at IS NULL"
                    )
                return cur.fetchone()["count"]
    except Exception as e:
        logger.error(f"[DB] Error counting consented buildings: {e}")
        return 0


# === API Client Operations ===


def save_api_client(
    company_name,
    contact_email,
    api_key_hash,
    tier="starter",
    rate_limit=100,
    allowed_cantons=None,
):
    try:
        import json

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO api_clients (company_name, contact_email, api_key_hash, tier, rate_limit_per_hour, allowed_cantons)
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
    except Exception as e:
        logger.error(f"[DB] Error saving API client: {e}")
        return None


def get_api_client_by_key(api_key_hash):
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM api_clients WHERE api_key_hash = %s AND active = TRUE",
                (api_key_hash,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"[DB] Error getting API client: {e}")
        return None


def track_api_usage(client_id, endpoint, params=None, response_size=0):
    try:
        import json

        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    INSERT INTO api_usage (client_id, endpoint, params, response_size)
                    VALUES (%s, %s, %s, %s)
                """,
                (client_id, endpoint, json.dumps(params or {}), response_size),
            )
            return True
    except Exception as e:
        logger.error(f"[DB] Error tracking API usage: {e}")
        return False


def get_api_usage_count(client_id, hours=1):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*) as count FROM api_usage
                    WHERE client_id = %s AND called_at > CURRENT_TIMESTAMP - INTERVAL '%s hours'
                """,
                    (client_id, hours),
                )
                return cur.fetchone()["count"]
    except Exception as e:
        logger.error(f"[DB] Error getting API usage count: {e}")
        return 0


# === Initialization check ===

_db_initialized = False


def update_document_signing_status(deepsign_document_id: str, status: str) -> bool:
    """Update LEG document signing status from DeepSign webhook."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE leg_documents SET signing_status = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE deepsign_document_id = %s
                """,
                    (status, deepsign_document_id),
                )
                return cur.rowcount > 0
    except Exception as e:
        logger.error(f"[DB] Error updating document signing status: {e}")
        return False


def store_leg_document(
    community_id: str, doc_type: str, pdf_bytes: bytes, filename: str
) -> int:
    """Store generated LEG document PDF."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO leg_documents (community_id, doc_type, filename, pdf_data)
                    VALUES (%s, %s, %s, %s) RETURNING id
                """,
                    (community_id, doc_type, filename, pdf_bytes),
                )
                return dict(cur.fetchone())["id"]
    except Exception as e:
        logger.error(f"[DB] Error storing leg document: {e}")
        return 0


def get_leg_document(doc_id: int) -> dict | None:
    """Get one stored LEG document including its PDF bytes."""
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT id, community_id, doc_type, filename, pdf_data,
                           signing_status, created_at
                    FROM leg_documents WHERE id = %s
                """,
                (doc_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"[DB] Error getting leg document: {e}")
        return None


def list_leg_documents(community_id: str) -> list[dict]:
    """List all documents for a community."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, doc_type, filename, signing_status, deepsign_document_id, created_at
                    FROM leg_documents WHERE community_id = %s ORDER BY created_at DESC
                """,
                    (community_id,),
                )
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error listing leg documents: {e}")
        return []


def save_lea_report(job_name: str, summary_text: str, status: str = "ok") -> bool:
    """Save an autonomous LEA report from a cron job webhook."""
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    INSERT INTO lea_reports (job_name, summary_text, status)
                    VALUES (%s, %s, %s)
                """,
                (job_name, summary_text, status),
            )
            return True
    except Exception as e:
        logger.error(f"[DB] Error saving LEA report: {e}")
        return False


def get_lea_reports(limit: int = 50) -> list[dict]:
    """Get recent LEA reports, newest first."""
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT id, job_name, created_at, summary_text, status
                    FROM lea_reports
                    ORDER BY created_at DESC
                    LIMIT %s
                """,
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error getting LEA reports: {e}")
        return []


def save_ops_snapshot(
    source: str,
    category: str,
    summary_text: str = "",
    status: str = "ok",
    payload: dict | None = None,
) -> bool:
    """Save a structured operator snapshot for the admin ops dashboard."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ops_snapshots (source, category, status, summary_text, payload)
                    VALUES (%s, %s, %s, %s, %s::jsonb)
                """,
                    (
                        source,
                        category,
                        status,
                        summary_text,
                        json.dumps(payload or {}),
                    ),
                )
                return True
    except Exception as e:
        logger.error(f"[DB] Error saving ops snapshot: {e}")
        return False


def get_ops_snapshots(
    limit: int = 50,
    source: str | None = None,
    category: str | None = None,
) -> list[dict]:
    """Get structured operator snapshots, newest first."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                where = []
                params: list = []
                if source:
                    where.append("source = %s")
                    params.append(source)
                if category:
                    where.append("category = %s")
                    params.append(category)
                query = """
                    SELECT id, source, category, status, summary_text, payload, created_at
                    FROM ops_snapshots
                """
                if where:
                    query += " WHERE " + " AND ".join(where)
                query += " ORDER BY created_at DESC LIMIT %s"
                params.append(limit)
                cur.execute(query, tuple(params))
                return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error getting ops snapshots: {e}")
        return []


def is_db_available() -> bool:
    """Check if PostgreSQL database is available."""
    global _db_initialized
    if not _db_initialized:
        _db_initialized = init_db()
        if _db_initialized:
            try:
                seed_default_tenant()
            except Exception as e:
                logger.warning(f"[DB] Could not seed default tenant: {e}")
    return _db_initialized and _connection_pool is not None


# ---------------------------------------------------------------------------
# Per-domain repository re-exports.
#
# Storage code for self-contained domains lives in `store/` and resolves the
# connection seam via `database.get_connection`. We re-export here so legacy
# callers (`import database as db; db.get_pv_profiles()`) and existing tests
# that monkeypatch `database.get_connection` keep working unchanged. The import
# is at module end to avoid a circular import (store.ranking imports database).
# ---------------------------------------------------------------------------
from store.billing import (  # noqa: F401
    get_active_communities,
    get_billing_period,
    get_billing_period_for_window,
    get_billing_policy,
    get_community_for_building,
    save_billing_period,
)
from store.correspondence import (  # noqa: F401
    list_correspondence,
    log_correspondence,
)
from store.email_queue import (  # noqa: F401
    cancel_emails_for_building,
    get_email_stats,
    get_pending_emails,
    mark_email_failed,
    mark_email_sent,
    schedule_email,
)
from store.formation_documents import replace_leg_document_bundle  # noqa: F401
from store.meter import (  # noqa: F401
    get_meter_reading_stats,
    get_meter_readings,
    save_meter_readings,
)
from store.metering import (  # noqa: F401
    get_community_metering_points,
    get_metering_point,
    get_metering_point_reading_stats,
    get_metering_point_readings,
    get_metering_points,
    get_period_readings,
    get_sdat_import,
    record_sdat_import,
    save_metering_point_readings,
    upsert_metering_points,
)
from store.profile import (  # noqa: F401
    get_all_municipality_profile_bfs_numbers,
    get_all_municipality_profiles,
    get_elcom_tariffs,
    get_municipality_profile,
    get_profile_bfs_missing_elcom_tariffs,
    get_sonnendach_municipal,
    save_elcom_tariffs,
    save_municipality_profile,
    save_sonnendach_municipal,
    search_municipality_profiles,
)
from store.ranking import (  # noqa: F401
    get_municipality_pv_panel,
    get_pv_movers,
    get_pv_profiles,
    save_municipality_pv_panel,
    upsert_municipality_pv,
)
from store.registry import (  # noqa: F401
    get_registry_entries_needing_verification,
    get_registry_entry,
    get_registry_entry_by_claim_token,
    get_registry_entry_by_slug,
    get_registry_entry_by_verification_token,
    get_registry_pending_count,
    list_registry_entries,
    mark_registry_entry_claimed,
    mark_registry_entry_verified,
    save_registry_entry,
    set_registry_claim_token,
    set_registry_verification_token,
    update_registry_entry_moderation,
)
from store.schema import create_tables
from store.tenant import (  # noqa: F401
    get_all_active_tenants,
    get_tenant_by_territory,
    seed_default_tenant,
    upsert_tenant,
)
from store.token import (  # noqa: F401
    delete_tokens_for_building,
    get_token,
    save_token,
    use_token,
)
from store.utility import (  # noqa: F401
    clear_utility_magic_token,
    get_all_utility_clients,
    get_utility_client,
    get_utility_client_by_email,
    get_utility_client_by_magic_token,
    get_utility_client_stats,
    save_utility_client,
    set_utility_magic_token,
    update_utility_client_api_key,
    update_utility_client_status,
)
