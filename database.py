"""
PostgreSQL Database Layer for BadenLEG
Replaces JSON file persistence with proper database storage.
"""
import os
import time
import logging
from contextlib import contextmanager
from typing import Optional, Dict, List, Any, Tuple

logger = logging.getLogger(__name__)

# Check for psycopg2
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from psycopg2 import pool
    HAS_POSTGRES = True
except ImportError:
    HAS_POSTGRES = False
    logger.warning("[DB] psycopg2 not installed, PostgreSQL features disabled")

# Database configuration
DATABASE_URL = os.getenv('DATABASE_URL', '')
DB_POOL_MIN = int(os.getenv('DB_POOL_MIN', '2'))
DB_POOL_MAX = int(os.getenv('DB_POOL_MAX', '10'))

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
            DB_POOL_MIN,
            DB_POOL_MAX,
            DATABASE_URL,
            cursor_factory=RealDictCursor
        )
        logger.info(f"[DB] Connection pool created (min={DB_POOL_MIN}, max={DB_POOL_MAX})")

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
    except Exception as e:
        if conn:
            conn.rollback()
        raise e
    finally:
        if conn:
            _connection_pool.putconn(conn)


def _create_tables():
    """Create database tables if they don't exist."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            # Users/Buildings table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS buildings (
                    building_id VARCHAR(64) PRIMARY KEY,
                    email VARCHAR(255) NOT NULL,
                    phone VARCHAR(32),
                    address TEXT NOT NULL,
                    lat DECIMAL(10, 7) NOT NULL,
                    lon DECIMAL(10, 7) NOT NULL,
                    plz VARCHAR(10),
                    building_type VARCHAR(64),
                    annual_consumption_kwh DECIMAL(12, 2),
                    potential_pv_kwp DECIMAL(8, 2),
                    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    verified BOOLEAN DEFAULT FALSE,
                    verified_at TIMESTAMP,
                    user_type VARCHAR(20) DEFAULT 'anonymous',
                    referrer_id VARCHAR(64),
                    referral_code VARCHAR(32) UNIQUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Consents table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS consents (
                    id SERIAL PRIMARY KEY,
                    building_id VARCHAR(64) REFERENCES buildings(building_id) ON DELETE CASCADE,
                    share_with_neighbors BOOLEAN DEFAULT FALSE,
                    share_with_utility BOOLEAN DEFAULT FALSE,
                    updates_opt_in BOOLEAN DEFAULT FALSE,
                    consent_version VARCHAR(16),
                    consent_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(building_id)
                )
            """)

            # Tokens table (verification and unsubscribe)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tokens (
                    token VARCHAR(128) PRIMARY KEY,
                    building_id VARCHAR(64) REFERENCES buildings(building_id) ON DELETE CASCADE,
                    token_type VARCHAR(20) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    used_at TIMESTAMP,
                    expires_at TIMESTAMP
                )
            """)

            # Clusters table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS clusters (
                    building_id VARCHAR(64) PRIMARY KEY REFERENCES buildings(building_id) ON DELETE CASCADE,
                    cluster_id INTEGER NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Cluster info table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS cluster_info (
                    cluster_id INTEGER PRIMARY KEY,
                    autarky_percent DECIMAL(5, 2),
                    num_members INTEGER,
                    polygon JSONB,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Referrals tracking table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS referrals (
                    id SERIAL PRIMARY KEY,
                    referrer_id VARCHAR(64) REFERENCES buildings(building_id) ON DELETE SET NULL,
                    referred_id VARCHAR(64) REFERENCES buildings(building_id) ON DELETE CASCADE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(referred_id)
                )
            """)

            # Analytics events table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS analytics_events (
                    id SERIAL PRIMARY KEY,
                    event_type VARCHAR(64) NOT NULL,
                    building_id VARCHAR(64),
                    data JSONB,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create indexes for common queries
            cur.execute("CREATE INDEX IF NOT EXISTS idx_buildings_email ON buildings(email)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_buildings_user_type ON buildings(user_type)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_buildings_verified ON buildings(verified)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_buildings_referrer ON buildings(referrer_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_tokens_building ON tokens(building_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_tokens_type ON tokens(token_type)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_clusters_cluster_id ON clusters(cluster_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_analytics_type ON analytics_events(event_type)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_analytics_created ON analytics_events(created_at)")

            logger.info("[DB] Tables and indexes created successfully")


# === Building Operations ===

def save_building(building_id: str, email: str, profile: Dict, consents: Dict,
                  user_type: str = 'anonymous', phone: str = None,
                  referrer_id: str = None) -> bool:
    """Save or update a building record."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Generate unique referral code
                import secrets
                referral_code = secrets.token_urlsafe(8)

                cur.execute("""
                    INSERT INTO buildings (
                        building_id, email, phone, address, lat, lon, plz,
                        building_type, annual_consumption_kwh, potential_pv_kwp,
                        registered_at, verified, verified_at, user_type,
                        referrer_id, referral_code
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        to_timestamp(%s), %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (building_id) DO UPDATE SET
                        email = EXCLUDED.email,
                        phone = EXCLUDED.phone,
                        verified = EXCLUDED.verified,
                        verified_at = EXCLUDED.verified_at,
                        user_type = EXCLUDED.user_type,
                        updated_at = CURRENT_TIMESTAMP
                """, (
                    building_id,
                    email,
                    phone,
                    profile.get('address', ''),
                    profile.get('lat'),
                    profile.get('lon'),
                    profile.get('plz'),
                    profile.get('building_type'),
                    profile.get('annual_consumption_kwh'),
                    profile.get('potential_pv_kwp'),
                    time.time(),
                    True,  # verified immediately for now
                    time.time(),
                    user_type,
                    referrer_id,
                    referral_code
                ))

                # Save consents
                cur.execute("""
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
                """, (
                    building_id,
                    consents.get('share_with_neighbors', False),
                    consents.get('share_with_utility', False),
                    consents.get('updates_opt_in', False),
                    consents.get('consent_version', '1.0'),
                    consents.get('consent_timestamp', time.time())
                ))

                # Track referral if present
                if referrer_id:
                    cur.execute("""
                        INSERT INTO referrals (referrer_id, referred_id)
                        VALUES (%s, %s)
                        ON CONFLICT (referred_id) DO NOTHING
                    """, (referrer_id, building_id))

                return True
    except Exception as e:
        logger.error(f"[DB] Error saving building {building_id}: {e}")
        return False


def get_building(building_id: str) -> Optional[Dict]:
    """Get a building record by ID."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT b.*, c.share_with_neighbors, c.share_with_utility,
                           c.updates_opt_in, c.consent_version
                    FROM buildings b
                    LEFT JOIN consents c ON b.building_id = c.building_id
                    WHERE b.building_id = %s
                """, (building_id,))
                row = cur.fetchone()
                if row:
                    return dict(row)
                return None
    except Exception as e:
        logger.error(f"[DB] Error getting building {building_id}: {e}")
        return None


def get_building_by_email(email: str) -> List[Dict]:
    """Find buildings by email address."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT building_id FROM buildings
                    WHERE LOWER(email) = LOWER(%s)
                """, (email,))
                return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error finding buildings by email: {e}")
        return []


def get_all_buildings() -> List[Dict]:
    """Get all buildings for map display."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT building_id, lat, lon, user_type, verified
                    FROM buildings
                    WHERE verified = TRUE
                """)
                return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error getting all buildings: {e}")
        return []


def get_all_building_profiles() -> List[Dict]:
    """Get all building profiles for ML clustering."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
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
        with get_connection() as conn:
            with conn.cursor() as cur:
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
                cur.execute("""
                    UPDATE buildings
                    SET verified = %s, verified_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                    WHERE building_id = %s
                """, (verified, building_id))
                return cur.rowcount > 0
    except Exception as e:
        logger.error(f"[DB] Error updating verification for {building_id}: {e}")
        return False


# === Token Operations ===

def save_token(token: str, building_id: str, token_type: str, ttl_seconds: int = 2592000) -> bool:
    """Save a verification or unsubscribe token (default TTL: 30 days)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO tokens (token, building_id, token_type, expires_at)
                    VALUES (%s, %s, %s, CURRENT_TIMESTAMP + INTERVAL '%s seconds')
                    ON CONFLICT (token) DO UPDATE SET
                        building_id = EXCLUDED.building_id,
                        token_type = EXCLUDED.token_type,
                        expires_at = EXCLUDED.expires_at
                """, (token, building_id, token_type, ttl_seconds))
                return True
    except Exception as e:
        logger.error(f"[DB] Error saving token: {e}")
        return False


def get_token(token: str) -> Optional[Dict]:
    """Get token info if valid (not expired, not used)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT * FROM tokens
                    WHERE token = %s
                    AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
                    AND used_at IS NULL
                """, (token,))
                row = cur.fetchone()
                if row:
                    return dict(row)
                return None
    except Exception as e:
        logger.error(f"[DB] Error getting token: {e}")
        return None


def use_token(token: str) -> bool:
    """Mark a token as used."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE tokens SET used_at = CURRENT_TIMESTAMP
                    WHERE token = %s
                """, (token,))
                return cur.rowcount > 0
    except Exception as e:
        logger.error(f"[DB] Error using token: {e}")
        return False


def delete_tokens_for_building(building_id: str, token_type: str = None) -> int:
    """Delete tokens for a building."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                if token_type:
                    cur.execute("""
                        DELETE FROM tokens
                        WHERE building_id = %s AND token_type = %s
                    """, (building_id, token_type))
                else:
                    cur.execute("""
                        DELETE FROM tokens WHERE building_id = %s
                    """, (building_id,))
                return cur.rowcount
    except Exception as e:
        logger.error(f"[DB] Error deleting tokens: {e}")
        return 0


# === Cluster Operations ===

def save_cluster(building_id: str, cluster_id: int) -> bool:
    """Save cluster assignment for a building."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO clusters (building_id, cluster_id)
                    VALUES (%s, %s)
                    ON CONFLICT (building_id) DO UPDATE SET
                        cluster_id = EXCLUDED.cluster_id,
                        updated_at = CURRENT_TIMESTAMP
                """, (building_id, cluster_id))
                return True
    except Exception as e:
        logger.error(f"[DB] Error saving cluster: {e}")
        return False


def save_cluster_info(cluster_id: int, info: Dict) -> bool:
    """Save cluster metadata."""
    try:
        import json
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO cluster_info (cluster_id, autarky_percent, num_members, polygon)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (cluster_id) DO UPDATE SET
                        autarky_percent = EXCLUDED.autarky_percent,
                        num_members = EXCLUDED.num_members,
                        polygon = EXCLUDED.polygon,
                        updated_at = CURRENT_TIMESTAMP
                """, (
                    cluster_id,
                    info.get('autarky_percent'),
                    info.get('num_members'),
                    json.dumps(info.get('polygon', []))
                ))
                return True
    except Exception as e:
        logger.error(f"[DB] Error saving cluster info: {e}")
        return False


def get_all_clusters() -> List[Dict]:
    """Get all clusters with their info."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT ci.cluster_id, ci.autarky_percent, ci.num_members, ci.polygon,
                           array_agg(c.building_id) as members
                    FROM cluster_info ci
                    LEFT JOIN clusters c ON ci.cluster_id = c.cluster_id
                    GROUP BY ci.cluster_id, ci.autarky_percent, ci.num_members, ci.polygon
                """)
                return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error getting clusters: {e}")
        return []


# === Referral Operations ===

def get_referral_code(building_id: str) -> Optional[str]:
    """Get the referral code for a building."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT referral_code FROM buildings WHERE building_id = %s
                """, (building_id,))
                row = cur.fetchone()
                if row:
                    return row['referral_code']
                return None
    except Exception as e:
        logger.error(f"[DB] Error getting referral code: {e}")
        return None


def get_building_by_referral_code(code: str) -> Optional[Dict]:
    """Find a building by its referral code."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT building_id, email, address FROM buildings
                    WHERE referral_code = %s
                """, (code,))
                row = cur.fetchone()
                if row:
                    return dict(row)
                return None
    except Exception as e:
        logger.error(f"[DB] Error finding building by referral code: {e}")
        return None


def get_referral_stats(building_id: str) -> Dict:
    """Get referral statistics for a building."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*) as total_referrals
                    FROM referrals WHERE referrer_id = %s
                """, (building_id,))
                row = cur.fetchone()
                return {
                    'total_referrals': row['total_referrals'] if row else 0
                }
    except Exception as e:
        logger.error(f"[DB] Error getting referral stats: {e}")
        return {'total_referrals': 0}


def get_referral_leaderboard(limit: int = 10) -> List[Dict]:
    """Get top referrers."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT b.building_id,
                           SPLIT_PART(b.address, ',', 1) as street,
                           COUNT(r.id) as referral_count
                    FROM buildings b
                    JOIN referrals r ON b.building_id = r.referrer_id
                    GROUP BY b.building_id, b.address
                    ORDER BY referral_count DESC
                    LIMIT %s
                """, (limit,))
                return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error getting leaderboard: {e}")
        return []


# === Analytics Operations ===

def track_event(event_type: str, building_id: str = None, data: Dict = None) -> bool:
    """Track an analytics event."""
    try:
        import json
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO analytics_events (event_type, building_id, data)
                    VALUES (%s, %s, %s)
                """, (event_type, building_id, json.dumps(data or {})))
                return True
    except Exception as e:
        logger.error(f"[DB] Error tracking event: {e}")
        return False


def get_stats() -> Dict:
    """Get platform statistics."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                stats = {}

                # Total buildings
                cur.execute("SELECT COUNT(*) as count FROM buildings WHERE verified = TRUE")
                stats['total_buildings'] = cur.fetchone()['count']

                # By type
                cur.execute("""
                    SELECT user_type, COUNT(*) as count
                    FROM buildings WHERE verified = TRUE
                    GROUP BY user_type
                """)
                for row in cur.fetchall():
                    stats[f'{row["user_type"]}_count'] = row['count']

                # Total referrals
                cur.execute("SELECT COUNT(*) as count FROM referrals")
                stats['total_referrals'] = cur.fetchone()['count']

                # Registrations today
                cur.execute("""
                    SELECT COUNT(*) as count FROM buildings
                    WHERE DATE(registered_at) = CURRENT_DATE
                """)
                stats['registrations_today'] = cur.fetchone()['count']

                return stats
    except Exception as e:
        logger.error(f"[DB] Error getting stats: {e}")
        return {}


# === Migration from JSON ===

def migrate_from_json(json_data: Dict) -> Tuple[int, int]:
    """
    Migrate data from JSON format to PostgreSQL.
    Returns (success_count, error_count).
    """
    success = 0
    errors = 0

    buildings = json_data.get('buildings', {})
    interest_pool = json_data.get('interest_pool', {})

    # Migrate registered buildings
    for building_id, data in buildings.items():
        try:
            profile = data.get('profile', {})
            consents = data.get('consents', {})

            save_building(
                building_id=building_id,
                email=data.get('email', ''),
                profile=profile,
                consents=consents,
                user_type='registered',
                phone=data.get('phone')
            )
            success += 1
        except Exception as e:
            logger.error(f"[MIGRATION] Error migrating building {building_id}: {e}")
            errors += 1

    # Migrate interest pool (anonymous)
    for building_id, data in interest_pool.items():
        try:
            profile = data.get('profile', {})
            consents = data.get('consents', {})

            save_building(
                building_id=building_id,
                email=data.get('email', ''),
                profile=profile,
                consents=consents,
                user_type='anonymous',
                phone=data.get('phone')
            )
            success += 1
        except Exception as e:
            logger.error(f"[MIGRATION] Error migrating interest {building_id}: {e}")
            errors += 1

    logger.info(f"[MIGRATION] Completed: {success} success, {errors} errors")
    return success, errors


# === Initialization check ===

_db_initialized = False

def is_db_available() -> bool:
    """Check if PostgreSQL database is available."""
    global _db_initialized
    if not _db_initialized:
        _db_initialized = init_db()
    return _db_initialized and _connection_pool is not None
