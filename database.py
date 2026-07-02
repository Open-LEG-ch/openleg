# SPDX-License-Identifier: AGPL-3.0-or-later
"""
PostgreSQL Database Layer for OpenLEG
Replaces JSON file persistence with proper database storage.
"""

import json
import os
import time
import logging
from contextlib import contextmanager
from typing import Optional, Dict, List, Tuple

logger = logging.getLogger(__name__)

# Check for psycopg2
try:
    from psycopg2.extras import RealDictCursor  # type: ignore
    from psycopg2 import pool  # type: ignore

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
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    city_id VARCHAR(64) DEFAULT 'zurich'
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

            # Communities table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS communities (
                    community_id VARCHAR(64) PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    admin_building_id VARCHAR(64) REFERENCES buildings(building_id),
                    distribution_model VARCHAR(20) DEFAULT 'simple',
                    description TEXT,
                    status VARCHAR(32) DEFAULT 'interested',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    formation_started_at TIMESTAMP,
                    dso_submitted_at TIMESTAMP,
                    dso_approved_at TIMESTAMP,
                    activated_at TIMESTAMP
                )
            """)

            # Community members table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS community_members (
                    id SERIAL PRIMARY KEY,
                    community_id VARCHAR(64) REFERENCES communities(community_id) ON DELETE CASCADE,
                    building_id VARCHAR(64) REFERENCES buildings(building_id) ON DELETE CASCADE,
                    role VARCHAR(20) DEFAULT 'member',
                    status VARCHAR(20) DEFAULT 'invited',
                    invited_by VARCHAR(64),
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    confirmed_at TIMESTAMP,
                    UNIQUE(community_id, building_id)
                )
            """)

            # Community documents table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS community_documents (
                    community_id VARCHAR(64) PRIMARY KEY REFERENCES communities(community_id) ON DELETE CASCADE,
                    documents JSONB DEFAULT '{}',
                    generated_at TIMESTAMP
                )
            """)

            # Webhooks table for utility integration
            cur.execute("""
                CREATE TABLE IF NOT EXISTS webhooks (
                    id SERIAL PRIMARY KEY,
                    webhook_type VARCHAR(32) NOT NULL,
                    url VARCHAR(512) NOT NULL,
                    secret VARCHAR(255),
                    events JSONB DEFAULT '[]',
                    active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_triggered_at TIMESTAMP,
                    failure_count INTEGER DEFAULT 0
                )
            """)

            # White-label configuration table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS white_label_configs (
                    id SERIAL PRIMARY KEY,
                    territory VARCHAR(64) UNIQUE NOT NULL,
                    utility_name VARCHAR(255),
                    logo_url VARCHAR(512),
                    primary_color VARCHAR(7),
                    secondary_color VARCHAR(7),
                    contact_email VARCHAR(255),
                    contact_phone VARCHAR(32),
                    legal_entity VARCHAR(255),
                    dso_contact VARCHAR(255),
                    active BOOLEAN DEFAULT TRUE,
                    config JSONB DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Scheduled emails table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS scheduled_emails (
                    id SERIAL PRIMARY KEY,
                    building_id VARCHAR(64) REFERENCES buildings(building_id) ON DELETE CASCADE,
                    email VARCHAR(255) NOT NULL,
                    template_key VARCHAR(64) NOT NULL,
                    send_at TIMESTAMP NOT NULL,
                    sent_at TIMESTAMP,
                    status VARCHAR(20) DEFAULT 'pending',
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Street leaderboard cache table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS street_stats (
                    street_name VARCHAR(255) PRIMARY KEY,
                    building_count INTEGER DEFAULT 0,
                    community_count INTEGER DEFAULT 0,
                    total_referrals INTEGER DEFAULT 0,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Municipalities table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS municipalities (
                    id SERIAL PRIMARY KEY,
                    bfs_number INTEGER UNIQUE,
                    name VARCHAR(255) NOT NULL,
                    kanton VARCHAR(2) DEFAULT 'ZH',
                    dso_name VARCHAR(255),
                    population INTEGER,
                    admin_email VARCHAR(255),
                    admin_building_id VARCHAR(64) REFERENCES buildings(building_id),
                    onboarding_status VARCHAR(32) DEFAULT 'pending',
                    data_agreement_signed_at TIMESTAMP,
                    subdomain VARCHAR(64) UNIQUE,
                    config JSONB DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Meter readings (15-min smart meter data)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS meter_readings (
                    id BIGSERIAL PRIMARY KEY,
                    building_id VARCHAR(64) REFERENCES buildings(building_id) ON DELETE CASCADE,
                    timestamp TIMESTAMP NOT NULL,
                    consumption_kwh DECIMAL(10, 4),
                    production_kwh DECIMAL(10, 4),
                    feed_in_kwh DECIMAL(10, 4),
                    source VARCHAR(32) DEFAULT 'csv',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(building_id, timestamp)
                )
            """)

            # Data consent tiers
            cur.execute("""
                CREATE TABLE IF NOT EXISTS data_consents (
                    id SERIAL PRIMARY KEY,
                    building_id VARCHAR(64) REFERENCES buildings(building_id) ON DELETE CASCADE,
                    tier INTEGER DEFAULT 1 CHECK (tier BETWEEN 1 AND 3),
                    share_with_municipality BOOLEAN DEFAULT TRUE,
                    share_anonymized_research BOOLEAN DEFAULT FALSE,
                    share_aggregated_providers BOOLEAN DEFAULT FALSE,
                    consent_version VARCHAR(16),
                    consented_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    revoked_at TIMESTAMP,
                    UNIQUE(building_id)
                )
            """)

            # B2B API clients
            cur.execute("""
                CREATE TABLE IF NOT EXISTS api_clients (
                    id SERIAL PRIMARY KEY,
                    company_name VARCHAR(255) NOT NULL,
                    contact_email VARCHAR(255) NOT NULL,
                    api_key_hash VARCHAR(128) UNIQUE NOT NULL,
                    tier VARCHAR(32) DEFAULT 'starter',
                    rate_limit_per_hour INTEGER DEFAULT 100,
                    allowed_cantons JSONB DEFAULT '["ZH"]',
                    active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # API usage tracking
            cur.execute("""
                CREATE TABLE IF NOT EXISTS api_usage (
                    id BIGSERIAL PRIMARY KEY,
                    client_id INTEGER REFERENCES api_clients(id),
                    endpoint VARCHAR(128) NOT NULL,
                    params JSONB,
                    response_size INTEGER,
                    called_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # ElCom tariffs (public data)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS elcom_tariffs (
                    id SERIAL PRIMARY KEY,
                    bfs_number INTEGER NOT NULL,
                    operator_name VARCHAR(255),
                    year INTEGER NOT NULL,
                    category VARCHAR(16) NOT NULL,
                    total_rp_kwh DECIMAL(10, 4),
                    energy_rp_kwh DECIMAL(10, 4),
                    grid_rp_kwh DECIMAL(10, 4),
                    municipality_fee_rp_kwh DECIMAL(10, 4),
                    kev_rp_kwh DECIMAL(10, 4),
                    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(bfs_number, operator_name, year, category)
                )
            """)

            # Municipality profiles (aggregated public data)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS municipality_profiles (
                    id SERIAL PRIMARY KEY,
                    bfs_number INTEGER UNIQUE NOT NULL,
                    name VARCHAR(255) NOT NULL,
                    kanton VARCHAR(2) DEFAULT 'ZH',
                    population INTEGER,
                    solar_potential_pct DECIMAL(6, 2),
                    solar_installed_kwp DECIMAL(12, 2),
                    ev_share_pct DECIMAL(6, 2),
                    renewable_heating_pct DECIMAL(6, 2),
                    electricity_consumption_mwh DECIMAL(12, 2),
                    renewable_production_mwh DECIMAL(12, 2),
                    leg_value_gap_chf DECIMAL(10, 2),
                    energy_transition_score DECIMAL(6, 2),
                    pv_score_pct DECIMAL(6, 2),
                    pv_estimated_potential_kw DECIMAL(14, 2),
                    pv_installed_kw DECIMAL(14, 2),
                    pv_untapped_kw DECIMAL(14, 2),
                    pv_annual_potential_gwh DECIMAL(12, 2),
                    pv_snapshot_year INTEGER,
                    pv_plant_match_rate DECIMAL(6, 2),
                    density_per_km2 DECIMAL(10, 2),
                    area_km2 DECIMAL(10, 2),
                    data_sources JSONB DEFAULT '{}',
                    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 10-year PV-Nutzungs-Panel (Quelle: dbm-leg-project, BFE-Anlagen kumuliert)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS municipality_pv_panel (
                    bfs_number INTEGER NOT NULL,
                    year INTEGER NOT NULL,
                    added_kw DECIMAL(14, 2),
                    added_plants INTEGER,
                    cumulative_kw DECIMAL(14, 2),
                    estimated_potential_kw DECIMAL(14, 2),
                    score_pct DECIMAL(8, 4),
                    untapped_kw DECIMAL(14, 2),
                    PRIMARY KEY (bfs_number, year)
                )
            """)

            # Sonnendach municipal solar data
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sonnendach_municipal (
                    id SERIAL PRIMARY KEY,
                    bfs_number INTEGER UNIQUE NOT NULL,
                    total_roof_area_m2 DECIMAL(14, 2),
                    suitable_roof_area_m2 DECIMAL(14, 2),
                    potential_kwh_year DECIMAL(14, 2),
                    potential_kwp DECIMAL(12, 2),
                    utilization_pct DECIMAL(6, 2),
                    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # PUBLIC-SNAPSHOT-PRIVATE-START: operator-insights-schema
            # Pre-computed insights cache
            cur.execute("""
                CREATE TABLE IF NOT EXISTS insights_cache (
                    id SERIAL PRIMARY KEY,
                    insight_type VARCHAR(64) NOT NULL,
                    scope VARCHAR(128),
                    period VARCHAR(32),
                    data JSONB NOT NULL,
                    computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP,
                    UNIQUE(insight_type, scope, period)
                )
            """)
            # PUBLIC-SNAPSHOT-PRIVATE-END: operator-insights-schema

            # Utility clients (B2B SaaS customers)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS utility_clients (
                    id SERIAL PRIMARY KEY,
                    client_id VARCHAR(64) UNIQUE NOT NULL,
                    company_name VARCHAR(255) NOT NULL,
                    contact_name VARCHAR(255),
                    contact_email VARCHAR(255) NOT NULL,
                    contact_phone VARCHAR(32),
                    vnb_name VARCHAR(255),
                    population INTEGER,
                    kanton VARCHAR(2),
                    tier VARCHAR(32) DEFAULT 'starter',
                    api_key_hash VARCHAR(128) UNIQUE,
                    status VARCHAR(32) DEFAULT 'pending',
                    magic_link_token VARCHAR(128),
                    magic_link_expires_at TIMESTAMP,
                    branding JSONB DEFAULT '{}',
                    billing_email VARCHAR(255),
                    stripe_customer_id VARCHAR(128),
                    onboarding_step INTEGER DEFAULT 0,
                    last_login_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # PUBLIC-SNAPSHOT-PRIVATE-START: operator-pipeline-schema
            # VNB research pipeline
            cur.execute("""
                CREATE TABLE IF NOT EXISTS vnb_research (
                    id SERIAL PRIMARY KEY,
                    vnb_name VARCHAR(255) NOT NULL,
                    bfs_numbers JSONB DEFAULT '[]',
                    kanton VARCHAR(2),
                    population_served INTEGER,
                    website VARCHAR(512),
                    contact_email VARCHAR(255),
                    contact_phone VARCHAR(32),
                    has_leg_offering BOOLEAN DEFAULT FALSE,
                    leg_offering_details TEXT,
                    competitor_status VARCHAR(64),
                    priority_score DECIMAL(5, 2) DEFAULT 0,
                    pipeline_status VARCHAR(32) DEFAULT 'researched',
                    outreach_notes TEXT,
                    last_contacted_at TIMESTAMP,
                    last_response_at TIMESTAMP,
                    research_data JSONB DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(vnb_name)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS vnb_pipeline (
                    id SERIAL PRIMARY KEY,
                    vnb_name VARCHAR(255) NOT NULL,
                    municipality VARCHAR(255),
                    bfs_number INTEGER,
                    population INTEGER,
                    score DECIMAL(5, 1),
                    status VARCHAR(32) DEFAULT 'lead',
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(vnb_name)
                )
            """)
            # PUBLIC-SNAPSHOT-PRIVATE-END: operator-pipeline-schema

            cur.execute("""
                CREATE TABLE IF NOT EXISTS billing_periods (
                    id SERIAL PRIMARY KEY,
                    community_id INTEGER NOT NULL,
                    period_start TIMESTAMP NOT NULL,
                    period_end TIMESTAMP NOT NULL,
                    total_production_kwh DECIMAL(12, 4) DEFAULT 0,
                    total_allocated_kwh DECIMAL(12, 4) DEFAULT 0,
                    total_surplus_kwh DECIMAL(12, 4) DEFAULT 0,
                    total_network_discount_chf DECIMAL(10, 2) DEFAULT 0,
                    distribution_model VARCHAR(32) DEFAULT 'proportional',
                    network_level VARCHAR(16) DEFAULT 'same',
                    status VARCHAR(32) DEFAULT 'draft',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS billing_line_items (
                    id SERIAL PRIMARY KEY,
                    billing_period_id INTEGER REFERENCES billing_periods(id),
                    participant_id VARCHAR(64) NOT NULL,
                    consumption_kwh DECIMAL(12, 4) DEFAULT 0,
                    allocated_kwh DECIMAL(12, 4) DEFAULT 0,
                    self_supply_ratio DECIMAL(5, 4) DEFAULT 0,
                    internal_cost_chf DECIMAL(10, 2) DEFAULT 0,
                    network_discount_chf DECIMAL(10, 2) DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS invoices (
                    id SERIAL PRIMARY KEY,
                    billing_period_id INTEGER REFERENCES billing_periods(id),
                    community_id INTEGER NOT NULL,
                    invoice_number VARCHAR(64) UNIQUE,
                    total_chf DECIMAL(10, 2) DEFAULT 0,
                    status VARCHAR(32) DEFAULT 'draft',
                    issued_at TIMESTAMP,
                    paid_at TIMESTAMP,
                    pdf_url TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS leg_documents (
                    id SERIAL PRIMARY KEY,
                    community_id INTEGER NOT NULL,
                    doc_type VARCHAR(64) NOT NULL,
                    filename VARCHAR(255),
                    pdf_data BYTEA,
                    signing_status VARCHAR(32) DEFAULT 'unsigned',
                    deepsign_document_id VARCHAR(128),
                    signed_pdf_url TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Migration: add stripe_subscription_id to utility_clients if missing
            cur.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'utility_clients' AND column_name = 'stripe_subscription_id'
                    ) THEN
                        ALTER TABLE utility_clients ADD COLUMN stripe_subscription_id VARCHAR(128);
                    END IF;
                END $$
            """)

            # Migration: add city_id to existing buildings table if missing
            cur.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'buildings' AND column_name = 'city_id'
                    ) THEN
                        ALTER TABLE buildings ADD COLUMN city_id VARCHAR(64) DEFAULT 'zurich';
                    END IF;
                END $$;
            """)

            # Create indexes for common queries
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_buildings_email ON buildings(email)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_buildings_user_type ON buildings(user_type)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_buildings_verified ON buildings(verified)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_buildings_referrer ON buildings(referrer_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_buildings_city_id ON buildings(city_id)"
            )

            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_tokens_building ON tokens(building_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_tokens_type ON tokens(token_type)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_clusters_cluster_id ON clusters(cluster_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_analytics_type ON analytics_events(event_type)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_analytics_created ON analytics_events(created_at)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_communities_status ON communities(status)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_communities_admin ON communities(admin_building_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_community_members_community ON community_members(community_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_community_members_building ON community_members(building_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_webhooks_type ON webhooks(webhook_type)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_webhooks_active ON webhooks(active)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_scheduled_emails_status ON scheduled_emails(status)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_scheduled_emails_send_at ON scheduled_emails(send_at)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_scheduled_emails_building ON scheduled_emails(building_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_municipalities_kanton ON municipalities(kanton)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_municipalities_subdomain ON municipalities(subdomain)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_meter_readings_building ON meter_readings(building_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_meter_readings_timestamp ON meter_readings(timestamp)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_meter_readings_building_time ON meter_readings(building_id, timestamp)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_data_consents_building ON data_consents(building_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_data_consents_tier ON data_consents(tier)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_clients_key ON api_clients(api_key_hash)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_usage_client ON api_usage(client_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_usage_called ON api_usage(called_at)"
            )
            # PUBLIC-SNAPSHOT-PRIVATE-START: operator-insights-index
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_insights_cache_type ON insights_cache(insight_type)"
            )
            # PUBLIC-SNAPSHOT-PRIVATE-END: operator-insights-index

            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_elcom_tariffs_bfs ON elcom_tariffs(bfs_number)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_elcom_tariffs_year ON elcom_tariffs(year)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_municipality_profiles_bfs ON municipality_profiles(bfs_number)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_municipality_profiles_kanton ON municipality_profiles(kanton)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_sonnendach_municipal_bfs ON sonnendach_municipal(bfs_number)"
            )

            # PV-Nutzungs-Spalten auf bestehende Profile nachziehen
            for column_ddl in (
                "pv_score_pct DECIMAL(6, 2)",
                "pv_estimated_potential_kw DECIMAL(14, 2)",
                "pv_installed_kw DECIMAL(14, 2)",
                "pv_untapped_kw DECIMAL(14, 2)",
                "pv_annual_potential_gwh DECIMAL(12, 2)",
                "pv_snapshot_year INTEGER",
                "pv_plant_match_rate DECIMAL(6, 2)",
                "density_per_km2 DECIMAL(10, 2)",
                "area_km2 DECIMAL(10, 2)",
            ):
                cur.execute(
                    f"ALTER TABLE municipality_profiles ADD COLUMN IF NOT EXISTS {column_ddl}"
                )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_municipality_profiles_pv_score ON municipality_profiles(pv_score_pct DESC)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_municipality_pv_panel_year ON municipality_pv_panel(year)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_municipality_pv_panel_bfs ON municipality_pv_panel(bfs_number)"
            )

            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_utility_clients_email ON utility_clients(contact_email)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_utility_clients_status ON utility_clients(status)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_utility_clients_tier ON utility_clients(tier)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_utility_clients_kanton ON utility_clients(kanton)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_utility_clients_magic_token ON utility_clients(magic_link_token)"
            )

            # PUBLIC-SNAPSHOT-PRIVATE-START: operator-pipeline-indexes
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_vnb_research_kanton ON vnb_research(kanton)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_vnb_research_status ON vnb_research(pipeline_status)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_vnb_research_priority ON vnb_research(priority_score DESC)"
            )

            # LEA autonomous reports
            cur.execute("""
                CREATE TABLE IF NOT EXISTS lea_reports (
                    id SERIAL PRIMARY KEY,
                    job_name VARCHAR(128) NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    summary_text TEXT,
                    status VARCHAR(32) DEFAULT 'ok'
                )
            """)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_lea_reports_job ON lea_reports(job_name)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_lea_reports_created ON lea_reports(created_at DESC)"
            )
            cur.execute("""
                CREATE TABLE IF NOT EXISTS ops_snapshots (
                    id SERIAL PRIMARY KEY,
                    source VARCHAR(64) NOT NULL,
                    category VARCHAR(64) NOT NULL,
                    status VARCHAR(32) DEFAULT 'ok',
                    summary_text TEXT,
                    payload JSONB DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_ops_snapshots_source ON ops_snapshots(source)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_ops_snapshots_category ON ops_snapshots(category)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_ops_snapshots_created ON ops_snapshots(created_at DESC)"
            )
            # PUBLIC-SNAPSHOT-PRIVATE-END: operator-pipeline-indexes

            logger.info("[DB] Tables and indexes created successfully")


# === Building Operations ===


def save_building(
    building_id: str,
    email: str,
    profile: Dict,
    consents: Dict,
    user_type: str = "anonymous",
    phone: Optional[str] = None,
    referrer_id: Optional[str] = None,
    city_id: Optional[str] = None,
) -> bool:
    """Save or update a building record."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
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


def get_building(building_id: str) -> Optional[Dict]:
    """Get a building record by ID."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
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


def get_building_by_email(email: str) -> List[Dict]:
    """Find buildings by email address."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
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


def get_all_buildings(city_id: Optional[str] = None) -> List[Dict]:
    """Get all buildings for map display, optionally scoped by city_id."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
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


# PUBLIC-SNAPSHOT-PRIVATE-START: operator-pipeline-helpers
def get_vnb_pipeline(status_filter: Optional[str] = None) -> List[Dict]:
    """Get VNB pipeline entries, optionally filtered by status."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                if status_filter:
                    cur.execute(
                        "SELECT * FROM vnb_pipeline WHERE status = %s ORDER BY score DESC NULLS LAST",
                        (status_filter,),
                    )
                else:
                    cur.execute(
                        "SELECT * FROM vnb_pipeline ORDER BY score DESC NULLS LAST"
                    )
                cols = [d[0] for d in cur.description] if cur.description else []
                return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"get_vnb_pipeline error: {e}")
        return []


def get_vnb_pipeline_stats() -> Dict:
    """Get pipeline funnel counts, average score, and conversion rate."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT status, COUNT(*)::int as count, ROUND(AVG(score)::numeric, 1) as avg_score
                    FROM vnb_pipeline GROUP BY status
                """)
                funnel = {
                    stage: 0
                    for stage in [
                        "lead",
                        "contacted",
                        "demo",
                        "trial",
                        "paid",
                        "churned",
                    ]
                }
                total = 0
                score_sum = 0.0
                score_count = 0
                for row in cur.fetchall():
                    status, count, avg_score = row
                    funnel[status] = count
                    total += count
                    if avg_score is not None:
                        score_sum += float(avg_score) * count
                        score_count += count
                paid = funnel.get("paid", 0)
                return {
                    "total": total,
                    "funnel": funnel,
                    "avg_score": round(score_sum / score_count, 1)
                    if score_count
                    else 0,
                    "conversion_rate": round(paid / total * 100, 1) if total else 0,
                }
    except Exception as e:
        logger.error(f"get_vnb_pipeline_stats error: {e}")
        return {"total": 0, "funnel": {}, "avg_score": 0, "conversion_rate": 0}


# PUBLIC-SNAPSHOT-PRIVATE-END: operator-pipeline-helpers


def get_all_building_profiles(city_id: Optional[str] = None) -> List[Dict]:
    """Get all building profiles for ML clustering, optionally scoped by city_id."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
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
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM buildings WHERE building_id = %s", (building_id,)
                )
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


# === Token Operations ===


def save_token(
    token: str, building_id: str, token_type: str, ttl_seconds: int = 2592000
) -> bool:
    """Save a verification or unsubscribe token (default TTL: 30 days)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO tokens (token, building_id, token_type, expires_at)
                    VALUES (%s, %s, %s, CURRENT_TIMESTAMP + INTERVAL '%s seconds')
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


def get_token(token: str) -> Optional[Dict]:
    """Get token info if valid (not expired, not used)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
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
    """Mark a token as used."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE tokens SET used_at = CURRENT_TIMESTAMP
                    WHERE token = %s
                """,
                    (token,),
                )
                return cur.rowcount > 0
    except Exception as e:
        logger.error(f"[DB] Error using token: {e}")
        return False


def delete_tokens_for_building(
    building_id: str, token_type: Optional[str] = None
) -> int:
    """Delete tokens for a building."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
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


# === Cluster Operations ===


def save_cluster(building_id: str, cluster_id: int) -> bool:
    """Save cluster assignment for a building."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
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


def save_cluster_info(cluster_id: int, info: Dict) -> bool:
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


def get_building_by_referral_code(code: str) -> Optional[Dict]:
    """Find a building by its referral code."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
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


def get_referral_stats(building_id: str) -> Dict:
    """Get referral statistics for a building."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
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


def get_referral_leaderboard(
    limit: int = 10, city_id: Optional[str] = None
) -> List[Dict]:
    """Get top referrers, optionally scoped by city_id."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
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
    event_type: str, building_id: Optional[str] = None, data: Optional[Dict] = None
) -> bool:
    """Track an analytics event."""
    try:
        import json

        with get_connection() as conn:
            with conn.cursor() as cur:
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


def get_stats(city_id: Optional[str] = None) -> Dict:
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


def migrate_from_json(json_data: Dict) -> Tuple[int, int]:
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
    lat: float, lon: float, radius_km: float = 0.5, city_id: Optional[str] = None
) -> int:
    """Count verified buildings within radius of a point, optionally scoped by city_id."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
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


def get_building_for_dashboard(building_id: str) -> Optional[Dict]:
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


# === Tenant Operations ===


def get_tenant_by_territory(territory: str) -> Optional[Dict]:
    """Get tenant config by territory slug."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM white_label_configs
                    WHERE territory = %s AND active = TRUE
                """,
                    (territory,),
                )
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        logger.error(f"[DB] Error getting tenant {territory}: {e}")
        return None


def get_all_active_tenants() -> List[Dict]:
    """Get all active tenant configs."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT territory, utility_name, primary_color, contact_email, active, config
                    FROM white_label_configs
                    WHERE active = TRUE
                    ORDER BY territory
                """)
                return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error getting active tenants: {e}")
        return []


def upsert_tenant(territory: str, config: Dict) -> bool:
    """Insert or update a tenant config."""
    try:
        import json

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO white_label_configs (
                        territory, utility_name, primary_color, secondary_color,
                        contact_email, contact_phone, legal_entity, dso_contact,
                        active, config
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (territory) DO UPDATE SET
                        utility_name = EXCLUDED.utility_name,
                        primary_color = EXCLUDED.primary_color,
                        secondary_color = EXCLUDED.secondary_color,
                        contact_email = EXCLUDED.contact_email,
                        contact_phone = EXCLUDED.contact_phone,
                        legal_entity = EXCLUDED.legal_entity,
                        dso_contact = EXCLUDED.dso_contact,
                        active = EXCLUDED.active,
                        config = EXCLUDED.config,
                        updated_at = CURRENT_TIMESTAMP
                """,
                    (
                        territory,
                        config.get("utility_name", ""),
                        config.get("primary_color", "#c7021a"),
                        config.get("secondary_color", "#f59e0b"),
                        config.get("contact_email", ""),
                        config.get("contact_phone", ""),
                        config.get("legal_entity", ""),
                        config.get("dso_contact", ""),
                        config.get("active", True),
                        json.dumps(
                            {
                                k: v
                                for k, v in config.items()
                                if k
                                not in (
                                    "utility_name",
                                    "primary_color",
                                    "secondary_color",
                                    "contact_email",
                                    "contact_phone",
                                    "legal_entity",
                                    "dso_contact",
                                    "active",
                                    "territory",
                                )
                            }
                        ),
                    ),
                )
                return True
    except Exception as e:
        logger.error(f"[DB] Error upserting tenant {territory}: {e}")
        return False


def seed_default_tenant() -> bool:
    """Seed the default Zurich tenant if it doesn't exist."""
    from tenant import DEFAULT_TENANT

    existing = get_tenant_by_territory("zurich")
    if existing:
        return True
    return upsert_tenant("zurich", DEFAULT_TENANT)


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
        with get_connection() as conn:
            with conn.cursor() as cur:
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
        with get_connection() as conn:
            with conn.cursor() as cur:
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
        with get_connection() as conn:
            with conn.cursor() as cur:
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

        with get_connection() as conn:
            with conn.cursor() as cur:
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


# PUBLIC-SNAPSHOT-PRIVATE-START: operator-insights-helpers
# === Insights Cache Operations ===


def save_insight(insight_type, scope, period, data, ttl_hours=24):
    try:
        import json

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO insights_cache (insight_type, scope, period, data, expires_at)
                    VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP + INTERVAL '%s hours')
                    ON CONFLICT (insight_type, scope, period) DO UPDATE SET
                        data = EXCLUDED.data,
                        computed_at = CURRENT_TIMESTAMP,
                        expires_at = EXCLUDED.expires_at
                """,
                    (insight_type, scope, period, json.dumps(data), ttl_hours),
                )
                return True
    except Exception as e:
        logger.error(f"[DB] Error saving insight: {e}")
        return False


def get_insight(insight_type, scope=None, period=None):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                query = "SELECT * FROM insights_cache WHERE insight_type = %s AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)"
                params = [insight_type]
                if scope:
                    query += " AND scope = %s"
                    params.append(scope)
                if period:
                    query += " AND period = %s"
                    params.append(period)
                cur.execute(query, params)
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        logger.error(f"[DB] Error getting insight: {e}")
        return None


# PUBLIC-SNAPSHOT-PRIVATE-END: operator-insights-helpers


# === Initialization check ===

_db_initialized = False

# PUBLIC-SNAPSHOT-PRIVATE-START: operator-research-helpers
# === VNB Research Operations ===


def save_vnb_research(vnb_name: str, data: Dict) -> bool:
    """Upsert VNB research record."""
    try:
        import json

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO vnb_research (
                        vnb_name, bfs_numbers, kanton, population_served, website,
                        contact_email, contact_phone, has_leg_offering, leg_offering_details,
                        competitor_status, priority_score, pipeline_status, research_data
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (vnb_name) DO UPDATE SET
                        bfs_numbers = EXCLUDED.bfs_numbers,
                        kanton = EXCLUDED.kanton,
                        population_served = EXCLUDED.population_served,
                        website = EXCLUDED.website,
                        contact_email = EXCLUDED.contact_email,
                        contact_phone = EXCLUDED.contact_phone,
                        has_leg_offering = EXCLUDED.has_leg_offering,
                        leg_offering_details = EXCLUDED.leg_offering_details,
                        competitor_status = EXCLUDED.competitor_status,
                        priority_score = EXCLUDED.priority_score,
                        pipeline_status = EXCLUDED.pipeline_status,
                        research_data = EXCLUDED.research_data,
                        updated_at = CURRENT_TIMESTAMP
                """,
                    (
                        vnb_name,
                        json.dumps(data.get("bfs_numbers", [])),
                        data.get("kanton", ""),
                        data.get("population_served"),
                        data.get("website", ""),
                        data.get("contact_email", ""),
                        data.get("contact_phone", ""),
                        data.get("has_leg_offering", False),
                        data.get("leg_offering_details", ""),
                        data.get("competitor_status", ""),
                        data.get("priority_score", 0),
                        data.get("pipeline_status", "researched"),
                        json.dumps(data.get("research_data", {})),
                    ),
                )
                return True
    except Exception as e:
        logger.error(f"[DB] Error saving VNB research: {e}")
        return False


def get_vnb_research(vnb_name: str) -> Optional[Dict]:
    """Get VNB research by name."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM vnb_research WHERE vnb_name = %s", (vnb_name,)
                )
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        logger.error(f"[DB] Error getting VNB research: {e}")
        return None


def get_all_vnb_research(
    pipeline_status: Optional[str] = None,
    kanton: Optional[str] = None,
    order_by: str = "priority_score",
) -> List[Dict]:
    """Get all VNB research records, optionally filtered."""
    allowed_orders = {"priority_score", "vnb_name", "population_served", "updated_at"}
    if order_by not in allowed_orders:
        order_by = "priority_score"
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                query = "SELECT * FROM vnb_research WHERE 1=1"
                params = []
                if pipeline_status:
                    query += " AND pipeline_status = %s"
                    params.append(pipeline_status)
                if kanton:
                    query += " AND kanton = %s"
                    params.append(kanton)
                query += f" ORDER BY {order_by} DESC"
                cur.execute(query, params)
                return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error getting VNB research: {e}")
        return []


def update_vnb_pipeline_status(
    vnb_name: str, status: str, notes: Optional[str] = None
) -> bool:
    """Update VNB pipeline status."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                if notes:
                    cur.execute(
                        """
                        UPDATE vnb_research
                        SET pipeline_status = %s, outreach_notes = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE vnb_name = %s
                    """,
                        (status, notes, vnb_name),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE vnb_research
                        SET pipeline_status = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE vnb_name = %s
                    """,
                        (status, vnb_name),
                    )
                return cur.rowcount > 0
    except Exception as e:
        logger.error(f"[DB] Error updating VNB pipeline status: {e}")
        return False


def get_vnb_research_stats() -> Dict:
    """Get VNB research pipeline statistics."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        COUNT(*) as total,
                        COUNT(*) FILTER (WHERE pipeline_status = 'researched') as researched,
                        COUNT(*) FILTER (WHERE pipeline_status = 'contacted') as contacted,
                        COUNT(*) FILTER (WHERE pipeline_status = 'responded') as responded,
                        COUNT(*) FILTER (WHERE pipeline_status = 'demo') as demo,
                        COUNT(*) FILTER (WHERE pipeline_status = 'pilot') as pilot,
                        COUNT(*) FILTER (WHERE pipeline_status = 'signed') as signed,
                        COUNT(*) FILTER (WHERE pipeline_status = 'active') as active,
                        COUNT(*) FILTER (WHERE has_leg_offering = TRUE) as has_leg,
                        AVG(priority_score) as avg_priority
                    FROM vnb_research
                """)
                return dict(cur.fetchone())
    except Exception as e:
        logger.error(f"[DB] Error getting VNB research stats: {e}")
        return {}


# PUBLIC-SNAPSHOT-PRIVATE-END: operator-research-helpers


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
    community_id: int, doc_type: str, pdf_bytes: bytes, filename: str
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
                return cur.fetchone()[0]
    except Exception as e:
        logger.error(f"[DB] Error storing leg document: {e}")
        return 0


def list_leg_documents(community_id: int) -> List[Dict]:
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


# PUBLIC-SNAPSHOT-PRIVATE-START: operator-report-helpers
def save_lea_report(job_name: str, summary_text: str, status: str = "ok") -> bool:
    """Save an autonomous LEA report from a cron job webhook."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
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


def get_lea_reports(limit: int = 50) -> List[Dict]:
    """Get recent LEA reports, newest first."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
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
    payload: Optional[Dict] = None,
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
    source: Optional[str] = None,
    category: Optional[str] = None,
) -> List[Dict]:
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


# PUBLIC-SNAPSHOT-PRIVATE-END: operator-report-helpers


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
from store.ranking import (  # noqa: E402, F401
    upsert_municipality_pv,
    save_municipality_pv_panel,
    get_pv_profiles,
    get_pv_movers,
    get_municipality_pv_panel,
)

from store.profile import (  # noqa: E402, F401
    save_elcom_tariffs,
    get_elcom_tariffs,
    save_municipality_profile,
    get_municipality_profile,
    get_all_municipality_profiles,
    get_all_municipality_profile_bfs_numbers,
    get_profile_bfs_missing_elcom_tariffs,
    save_sonnendach_municipal,
    get_sonnendach_municipal,
)

from store.billing import (  # noqa: E402, F401
    save_billing_period,
    get_active_communities,
    get_community_for_building,
    get_billing_period,
)

from store.email_queue import (  # noqa: E402, F401
    schedule_email,
    get_pending_emails,
    mark_email_sent,
    mark_email_failed,
    cancel_emails_for_building,
    get_email_stats,
)

from store.utility import (  # noqa: E402, F401
    save_utility_client,
    get_utility_client,
    get_utility_client_by_email,
    get_utility_client_by_magic_token,
    set_utility_magic_token,
    clear_utility_magic_token,
    update_utility_client_status,
    update_utility_client_api_key,
    get_all_utility_clients,
    get_utility_client_stats,
)

from store.meter import (  # noqa: E402, F401
    save_meter_readings,
    get_meter_readings,
    get_meter_reading_stats,
)
