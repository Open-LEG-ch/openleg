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
from billing_approval import BillingApprovalError  # noqa: F401
from store import operator_api as _operator_api
from store import operator_operations as _operator_operations
from store import vnb_exchange as _vnb_exchange
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
    BillingPolicyConflict,
    BillingStoreError,
    approve_billing_period,
    cancel_invoice,
    complete_invoice_delivery,
    confirm_invoice_delivery,
    correct_invoice,
    fail_invoice_delivery,
    get_active_communities,
    get_billing_period,
    get_billing_period_for_window,
    get_billing_policy,
    get_community_for_building,
    get_invoice_for_participant,
    get_invoices_for_participant,
    list_bank_statement_entries,
    list_billing_periods,
    list_billing_policies,
    list_community_billing_periods,
    list_community_invoice_events,
    list_community_invoices,
    list_invoice_events,
    prepare_invoice_delivery,
    reconcile_bank_statement,
    record_billing_period_preparer,
    record_invoice_payment,
    save_billing_period,
    save_billing_policy,
)
from store.building import (  # noqa: F401
    NEIGHBOR_BOX_HALF_WIDTH_KM,
    VerifiedRegistrationConflict,
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
from store.calculated_values import (  # noqa: F401
    find_overlapping_calculated_values,
    get_calculated_values_community,
    get_validated_calculated_values,
    list_calculated_values_deliveries,
    save_calculated_values_delivery,
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
    cleanup_finished_emails,
    get_email_stats,
    get_pending_emails,
    mark_email_failed,
    mark_email_sent,
    schedule_email,
)
from store.formation import (  # noqa: F401
    confirm_invited_member,
    count_confirmed_members,
    create_community_record,
    fetch_community_with_members,
    fetch_nearby_consenting_neighbours,
    fetch_user_communities,
    insert_invited_member,
    mark_formation_started,
    set_community_dual_control,
    set_member_access_roles,
    submit_community_to_dso,
)
from store.formation_documents import replace_leg_document_bundle  # noqa: F401
from store.interest import (  # noqa: F401
    cleanup_expired_interest,
    get_interest_count,
    get_interest_counts_by_bfs,
    get_municipality_interest_summary,
    get_operator_interest_counts,
    get_operator_interest_records,
    get_verified_interest_recipients,
    save_coverage_request,
    verify_coverage_request,
)
from store.invoice_query import (  # noqa: F401
    add_invoice_query_message,
    list_invoice_queries,
    open_invoice_query,
    transition_invoice_query,
    update_invoice_query,
)
from store.meter import (  # noqa: F401
    get_meter_reading_stats,
    get_meter_readings,
    save_meter_readings,
)
from store.metering import (  # noqa: F401
    get_billable_period_snapshot,
    get_community_metering_points,
    get_metering_point,
    get_metering_point_reading_stats,
    get_metering_point_readings,
    get_metering_points,
    get_period_readings,
    get_sdat_import,
    get_sdat_import_index,
    get_sdat_veracity_flags,
    get_unassigned_period_metering_point_ids,
    record_sdat_import,
    record_sdat_veracity_flags,
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
from store.sdat_ingestion import (  # noqa: F401
    acquire_sdat_ingestion_lock,
    list_sdat_ingestion_schedules,
    record_sdat_ingestion_run,
    release_sdat_ingestion_lock,
    upsert_sdat_ingestion_schedule,
)
from store.tenant import (  # noqa: F401
    get_all_active_tenants,
    get_tenant_by_territory,
    seed_default_tenant,
    upsert_tenant,
)
from store.token import (  # noqa: F401
    VerificationConflict,
    confirm_building_interest,
    confirm_profile_deletion,
    create_coverage_deletion_tokens,
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

create_operator_api_client = _operator_api.create_client
claim_operator_api_usage = _operator_api.claim_usage
create_operator_event = _operator_api.create_event
get_operator_api_client_by_token_hash = _operator_api.get_client_by_token_hash
get_pending_webhook_deliveries = _operator_api.get_pending_deliveries
list_operator_api_clients = _operator_api.list_clients
list_operator_webhook_deliveries = _operator_api.list_deliveries
record_webhook_attempt = _operator_api.record_attempt
record_operator_api_usage = _operator_api.record_usage
revoke_operator_api_client = _operator_api.revoke_client
rotate_operator_api_client = _operator_api.rotate_client
retry_operator_webhook_delivery = _operator_api.retry_delivery
get_operator_api_usage_count = _operator_api.usage_count

VnbExchangeStoreError = _vnb_exchange.VnbExchangeStoreError
VnbSubmissionConflict = _vnb_exchange.VnbSubmissionConflict
claim_vnb_submission = _vnb_exchange.claim
get_vnb_submission_case = _vnb_exchange.get_case
get_vnb_manual_package = _vnb_exchange.get_manual_package
list_vnb_submission_cases = _vnb_exchange.list_cases
mark_vnb_manual_delivered = _vnb_exchange.mark_manual_delivered
record_vnb_submission_outcome = _vnb_exchange.record_outcome
claim_vnb_mutation = _vnb_exchange.claim_mutation
record_vnb_mutation_outcome = _vnb_exchange.record_mutation_outcome
list_vnb_mutations = _vnb_exchange.list_mutations
get_vnb_mutation_manual_package = _vnb_exchange.get_mutation_manual_package
mark_vnb_mutation_manual_delivered = _vnb_exchange.mark_mutation_manual_delivered
record_vnb_mutation_response = _vnb_exchange.record_mutation_response
list_operator_metering_jobs = _operator_operations.list_metering_jobs
get_operator_ingestion_retry = _operator_operations.get_ingestion_retry
claim_operator_ingestion_retry = _operator_operations.claim_ingestion_retry
complete_operator_ingestion_retry = _operator_operations.complete_ingestion_retry
release_operator_ingestion_retry = _operator_operations.release_ingestion_retry
list_operator_calculated_deliveries = _operator_operations.list_calculated_deliveries
list_operator_billing_periods = _operator_operations.list_periods
list_operator_invoices = _operator_operations.list_invoices
list_operator_invoice_cases = _operator_operations.list_cases
list_operator_payment_matches = _operator_operations.list_payments
respond_operator_invoice_case = _operator_operations.respond_case
confirm_operator_payment_match = _operator_operations.confirm_payment
