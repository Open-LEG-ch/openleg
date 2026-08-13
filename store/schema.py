# SPDX-License-Identifier: AGPL-3.0-or-later
"""Database schema creation and inline migrations."""

import logging

logger = logging.getLogger(__name__)


def _get_connection():
    import database

    return database.get_connection()


def create_tables():
    """Create tables and indexes, then run inline idempotent migrations."""
    with _get_connection() as conn:
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

            cur.execute("""
                CREATE TABLE IF NOT EXISTS dashboard_access_tokens (
                    token_hash CHAR(64) PRIMARY KEY,
                    building_id VARCHAR(64) NOT NULL REFERENCES buildings(building_id) ON DELETE CASCADE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP NOT NULL,
                    used_at TIMESTAMP,
                    revoked_at TIMESTAMP
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

            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_dashboard_access_tokens_building_id
                ON dashboard_access_tokens(building_id)
            """)

            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_dashboard_access_tokens_expires_at
                ON dashboard_access_tokens(expires_at)
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
                    onboarding_step INTEGER DEFAULT 0,
                    last_login_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Open LEG registry: self-submitted, human-moderated directory of
            # Swiss LEGs, independent of which platform (if any) formed them.
            # See docs/leg-registry.md for the product contract.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS leg_registry (
                    id SERIAL PRIMARY KEY,
                    slug VARCHAR(128) UNIQUE NOT NULL,
                    name VARCHAR(255) NOT NULL,
                    kanton VARCHAR(2),
                    plz VARCHAR(10),
                    ort VARCHAR(255),
                    bfs_number INTEGER,
                    vnb_name VARCHAR(255),
                    member_count_estimate INTEGER,
                    leg_status VARCHAR(32) DEFAULT 'planung',
                    description TEXT,
                    website_url VARCHAR(512),
                    contact_email VARCHAR(255) NOT NULL,
                    moderation_status VARCHAR(32) DEFAULT 'pending',
                    moderation_note TEXT,
                    source VARCHAR(32) DEFAULT 'self_submitted',
                    community_id VARCHAR(64)
                        REFERENCES communities(community_id) ON DELETE SET NULL,
                    claim_token VARCHAR(128),
                    claim_token_expires_at TIMESTAMP,
                    claimed_at TIMESTAMP,
                    claimed_by_email VARCHAR(255),
                    last_verified_at TIMESTAMP,
                    verification_token VARCHAR(128),
                    verification_token_expires_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Migration: add verification_token columns to leg_registry if missing
            # (leg_registry shipped in Phase 1 without these Phase 2 columns).
            cur.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'leg_registry' AND column_name = 'verification_token'
                    ) THEN
                        ALTER TABLE leg_registry ADD COLUMN verification_token VARCHAR(128);
                        ALTER TABLE leg_registry ADD COLUMN verification_token_expires_at TIMESTAMP;
                    END IF;
                END $$
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS billing_tariffs (
                    id SERIAL PRIMARY KEY,
                    community_id VARCHAR(64) NOT NULL REFERENCES communities(community_id),
                    effective_from TIMESTAMPTZ NOT NULL,
                    effective_to TIMESTAMPTZ,
                    internal_price_chf_per_kwh DECIMAL(12, 6) NOT NULL CHECK (internal_price_chf_per_kwh >= 0),
                    grid_fee_chf_per_kwh DECIMAL(12, 6) NOT NULL CHECK (grid_fee_chf_per_kwh >= 0),
                    network_level VARCHAR(16) NOT NULL CHECK (network_level IN ('same', 'cross')),
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(community_id, effective_from)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS billing_periods (
                    id SERIAL PRIMARY KEY,
                    community_id VARCHAR(64) NOT NULL,
                    period_start TIMESTAMPTZ NOT NULL,
                    period_end TIMESTAMPTZ NOT NULL,
                    total_production_kwh DECIMAL(12, 4) DEFAULT 0,
                    total_allocated_kwh DECIMAL(12, 4) DEFAULT 0,
                    total_surplus_kwh DECIMAL(12, 4) DEFAULT 0,
                    total_network_discount_chf DECIMAL(10, 2) DEFAULT 0,
                    distribution_model VARCHAR(32) DEFAULT 'proportional',
                    network_level VARCHAR(16) DEFAULT 'same',
                    internal_price_chf_per_kwh DECIMAL(12, 6),
                    grid_fee_chf_per_kwh DECIMAL(12, 6),
                    timezone VARCHAR(64) NOT NULL DEFAULT 'Europe/Zurich',
                    input_fingerprint VARCHAR(64),
                    source_document_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                    reconciliation JSONB NOT NULL DEFAULT '{}'::jsonb,
                    status VARCHAR(32) DEFAULT 'draft',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(community_id, period_start, period_end)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS billing_line_items (
                    id SERIAL PRIMARY KEY,
                    billing_period_id INTEGER REFERENCES billing_periods(id),
                    participant_id VARCHAR(64) NOT NULL,
                    item_type VARCHAR(32),
                    quantity_kwh DECIMAL(12, 6),
                    unit_price_chf_per_kwh DECIMAL(12, 6),
                    amount_chf DECIMAL(12, 6),
                    consumption_kwh DECIMAL(12, 4) DEFAULT 0,
                    allocated_kwh DECIMAL(12, 4) DEFAULT 0,
                    self_supply_ratio DECIMAL(5, 4) DEFAULT 0,
                    internal_cost_chf DECIMAL(10, 2) DEFAULT 0,
                    network_discount_chf DECIMAL(10, 2) DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cur.execute("""
                ALTER TABLE billing_periods
                    ADD COLUMN IF NOT EXISTS internal_price_chf_per_kwh DECIMAL(12, 6),
                    ADD COLUMN IF NOT EXISTS grid_fee_chf_per_kwh DECIMAL(12, 6),
                    ADD COLUMN IF NOT EXISTS timezone VARCHAR(64) NOT NULL DEFAULT 'Europe/Zurich',
                    ADD COLUMN IF NOT EXISTS input_fingerprint VARCHAR(64),
                    ADD COLUMN IF NOT EXISTS source_document_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                    ADD COLUMN IF NOT EXISTS reconciliation JSONB NOT NULL DEFAULT '{}'::jsonb
            """)

            cur.execute("""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'billing_periods'
                          AND column_name = 'period_start'
                          AND data_type = 'timestamp without time zone'
                    ) THEN
                        ALTER TABLE billing_periods
                            ALTER COLUMN period_start TYPE TIMESTAMPTZ
                                USING period_start AT TIME ZONE 'Europe/Zurich',
                            ALTER COLUMN period_end TYPE TIMESTAMPTZ
                                USING period_end AT TIME ZONE 'Europe/Zurich';
                    END IF;
                END $$
            """)

            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_billing_period_community_window
                ON billing_periods (community_id, period_start, period_end)
            """)

            cur.execute("""
                ALTER TABLE billing_line_items
                    ADD COLUMN IF NOT EXISTS item_type VARCHAR(32),
                    ADD COLUMN IF NOT EXISTS quantity_kwh DECIMAL(12, 6),
                    ADD COLUMN IF NOT EXISTS unit_price_chf_per_kwh DECIMAL(12, 6),
                    ADD COLUMN IF NOT EXISTS amount_chf DECIMAL(12, 6)
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS invoices (
                    id SERIAL PRIMARY KEY,
                    billing_period_id INTEGER REFERENCES billing_periods(id),
                    community_id VARCHAR(64) NOT NULL,
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
                    community_id VARCHAR(64) NOT NULL,
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

            # Migration: align community_id columns with communities.community_id
            # (VARCHAR(64) UUID strings). billing_periods, invoices and
            # leg_documents historically declared INTEGER, making the join to
            # communities impossible. INTEGER -> VARCHAR is a safe widening
            # cast for any pre-existing rows.
            cur.execute("""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'billing_periods'
                            AND column_name = 'community_id'
                            AND data_type = 'integer'
                    ) THEN
                        ALTER TABLE billing_periods
                            ALTER COLUMN community_id TYPE VARCHAR(64);
                    END IF;
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'invoices'
                            AND column_name = 'community_id'
                            AND data_type = 'integer'
                    ) THEN
                        ALTER TABLE invoices
                            ALTER COLUMN community_id TYPE VARCHAR(64);
                    END IF;
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'leg_documents'
                            AND column_name = 'community_id'
                            AND data_type = 'integer'
                    ) THEN
                        ALTER TABLE leg_documents
                            ALTER COLUMN community_id TYPE VARCHAR(64);
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

            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_leg_registry_moderation_status ON leg_registry(moderation_status)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_leg_registry_kanton ON leg_registry(kanton)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_leg_registry_plz ON leg_registry(plz)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_leg_registry_claim_token ON leg_registry(claim_token)"
            )

            # Community correspondence ledger: shared in/out mail log per LEG
            # (email and physical post, manually logged). Phase 6 MVP, see
            # docs/leg-registry.md.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS correspondence_log (
                    id SERIAL PRIMARY KEY,
                    community_id VARCHAR(64) NOT NULL,
                    direction VARCHAR(8) NOT NULL,
                    channel VARCHAR(16) NOT NULL,
                    counterparty VARCHAR(255),
                    subject VARCHAR(255),
                    notes TEXT,
                    logged_by VARCHAR(64),
                    attachment_filename VARCHAR(255),
                    attachment_mime VARCHAR(64),
                    attachment_data BYTEA,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute(
                "ALTER TABLE correspondence_log ADD COLUMN IF NOT EXISTS attachment_filename VARCHAR(255)"
            )
            cur.execute(
                "ALTER TABLE correspondence_log ADD COLUMN IF NOT EXISTS attachment_mime VARCHAR(64)"
            )
            cur.execute(
                "ALTER TABLE correspondence_log ADD COLUMN IF NOT EXISTS attachment_data BYTEA"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_correspondence_log_community ON correspondence_log(community_id)"
            )

            # LEA autonomous reports (instance ops, posted via /api/internal/*)
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

            cur.execute("""
                CREATE TABLE IF NOT EXISTS metering_points (
                    metering_point_id VARCHAR(64) PRIMARY KEY,
                    vnb_community_id VARCHAR(64),
                    community_id VARCHAR(64) REFERENCES communities(community_id)
                        ON DELETE SET NULL,
                    building_id VARCHAR(64) REFERENCES buildings(building_id)
                        ON DELETE SET NULL,
                    alias VARCHAR(128),
                    address TEXT,
                    active BOOLEAN DEFAULT TRUE,
                    first_seen_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_metering_points_building ON metering_points(building_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_metering_points_community ON metering_points(community_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_metering_points_vnb_community ON metering_points(vnb_community_id)"
            )

            cur.execute("""
                CREATE TABLE IF NOT EXISTS metering_point_readings (
                    id BIGSERIAL PRIMARY KEY,
                    metering_point_id VARCHAR(64) NOT NULL
                        REFERENCES metering_points(metering_point_id)
                        ON DELETE CASCADE,
                    direction VARCHAR(16) NOT NULL
                        CHECK (direction IN ('consumption', 'production')),
                    measured_at TIMESTAMPTZ NOT NULL,
                    resolution_minutes SMALLINT NOT NULL DEFAULT 15,
                    total_kwh NUMERIC(12, 4),
                    grid_kwh NUMERIC(12, 4),
                    community_kwh NUMERIC(12, 4),
                    condition_code VARCHAR(8),
                    source_document_id VARCHAR(64),
                    imported_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE (metering_point_id, direction, measured_at)
                )
            """)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_mpr_measured_at ON metering_point_readings(measured_at)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_mpr_document ON metering_point_readings(source_document_id)"
            )

            cur.execute("""
                CREATE TABLE IF NOT EXISTS sdat_imports (
                    id SERIAL PRIMARY KEY,
                    document_id VARCHAR(64) NOT NULL UNIQUE,
                    doc_type VARCHAR(8),
                    file_name VARCHAR(255),
                    vnb_community_id VARCHAR(64),
                    document_created_at TIMESTAMPTZ,
                    period_start TIMESTAMPTZ,
                    period_end TIMESTAMPTZ,
                    block_count INTEGER DEFAULT 0,
                    row_count INTEGER DEFAULT 0,
                    new_count INTEGER DEFAULT 0,
                    corrected_count INTEGER DEFAULT 0,
                    imported_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_sdat_imports_period ON sdat_imports(period_start)"
            )

            logger.info("[DB] Tables and indexes created successfully")
