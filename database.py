# SPDX-License-Identifier: AGPL-3.0-or-later
"""
PostgreSQL Database Layer for OpenLEG
Replaces JSON file persistence with proper database storage.
"""

import logging
import os
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


_db_initialized = False


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
from store.access_token import (  # noqa: F401
    consume_dashboard_access_token,
    consume_municipality_access_token,
    revoke_dashboard_access_tokens,
    revoke_municipality_access_tokens,
    save_dashboard_access_token,
    save_municipality_access_token,
)
from store.analytics import get_stats, track_event  # noqa: F401
from store.api_client import (  # noqa: F401
    get_api_client_by_key,
    get_api_usage_count,
    save_api_client,
    track_api_usage,
)
from store.billing import (  # noqa: F401
    BillingStoreError,
    get_active_communities,
    get_billing_period,
    get_billing_period_for_window,
    get_billing_policy,
    get_community_for_building,
    list_billing_periods,
    save_billing_period,
)
from store.building import (  # noqa: F401
    NEIGHBOR_BOX_HALF_WIDTH_KM,
    delete_building,
    get_all_building_profiles,
    get_all_buildings,
    get_building,
    get_building_by_email,
    get_building_for_dashboard,
    get_neighbor_count_near,
    get_operator_building_profiles,
    save_building,
    update_building_verified,
)
from store.cluster import (  # noqa: F401
    save_cluster,
    save_cluster_info,
)
from store.consent import (  # noqa: F401
    count_consented_buildings,
    get_data_consent,
    save_data_consent,
)
from store.correspondence import (  # noqa: F401
    get_correspondence_attachment,
    list_correspondence,
    log_correspondence,
)
from store.dashboard_profile import update_dashboard_profile  # noqa: F401
from store.document import (  # noqa: F401
    get_leg_document,
    list_leg_documents,
    store_leg_document,
    update_document_signing_status,
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
    get_sdat_import_index,
    get_unassigned_period_metering_point_ids,
    record_sdat_import,
    save_metering_point_readings,
    upsert_metering_points,
)
from store.municipality import (  # noqa: F401
    get_all_municipalities,
    get_municipality,
    get_municipality_by_admin_email,
    save_municipality,
    update_municipality_status,
)
from store.ops import (  # noqa: F401
    get_lea_reports,
    get_ops_snapshots,
    save_lea_report,
    save_ops_snapshot,
)
from store.profile import (  # noqa: F401
    get_all_elcom_tariffs,
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
from store.referral import (  # noqa: F401
    get_building_by_referral_code,
    get_referral_code,
    get_referral_leaderboard,
    get_referral_stats,
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
    confirm_profile_deletion,
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
