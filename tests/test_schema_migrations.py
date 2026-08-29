# SPDX-License-Identifier: AGPL-3.0-or-later
"""The guarded migrations, executed against a table in its old shape.

`store/schema.py` carries hand-written migrations, each behind an
`information_schema` existence check. Nothing had ever run one: the schema tests
fake the cursor or match the migration SQL as source text, and the only
database-backed test in the suite runs against a freshly created container,
where every existence check evaluates against a database that never had the old
shape. The `ALTER` branches were unreachable by anything in this repository.

The same disposable PostgreSQL harness also covers repository queries whose
privacy or scoping contract cannot be proved with fake-cursor SQL assertions.

Two structural traps follow from the design, and this test is the only thing
that would notice either one:

- a column added only inside a `CREATE TABLE IF NOT EXISTS` block never reaches
  a deployed table, because Postgres skips the whole statement once the table
  exists
- a type change needs its own guarded `ALTER`, written by hand, per column

Marked `integration`, so it runs in CI against the disposable `postgres:16`
service and skips everywhere else. It creates a temporary database, builds a
pre-migration `billing_periods` table inside it, and lets `create_tables()`
migrate that table forward.
"""

import os
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras
import psycopg2.pool
import pytest

import billing_policy

PRE_MIGRATION_BILLING_PERIODS = """
    CREATE TABLE billing_periods (
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
        created_at TIMESTAMPTZ DEFAULT NOW()
    )
"""

PRE_MIGRATION_METERING_POINTS = """
    CREATE TABLE metering_points (
        metering_point_id VARCHAR(64) PRIMARY KEY,
        vnb_community_id VARCHAR(64),
        community_id VARCHAR(64),
        building_id VARCHAR(64),
        alias VARCHAR(128),
        address TEXT,
        active BOOLEAN DEFAULT TRUE,
        first_seen_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    )
"""

PRE_MIGRATION_INVOICES = """
    CREATE TABLE invoices (
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
"""


def _column(cur, table, column):
    cur.execute(
        """
        SELECT data_type FROM information_schema.columns
        WHERE table_name = %s AND column_name = %s
        """,
        (table, column),
    )
    row = cur.fetchone()
    return row["data_type"] if row else None


def _constraint(cur, table, constraint_name):
    cur.execute(
        """
        SELECT conname FROM pg_constraint
        WHERE conrelid = %s::regclass AND conname = %s
        """,
        (table, constraint_name),
    )
    return cur.fetchone()


def _drop_invoice_schema_for_legacy_fixture(cur):
    """Remove fresh tables that depend on invoices before installing old DDL."""
    cur.execute(
        """
        DROP TABLE invoice_corrections, invoice_delivery_jobs,
                   invoice_lifecycle_events, invoices
        """
    )


@contextmanager
def _temporary_database():
    """Create a throw-away Postgres database and yield its URL."""
    original_url = os.environ["DATABASE_URL"]
    parsed = urlsplit(original_url)
    admin_url = urlunsplit(parsed._replace(path="/postgres"))
    db_name = f"openleg_migration_{secrets.token_hex(6)}"
    db_url = urlunsplit(parsed._replace(path=f"/{db_name}"))

    admin_conn = psycopg2.connect(admin_url)
    admin_conn.autocommit = True
    try:
        with admin_conn.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        admin_conn.close()

    try:
        yield db_url
    finally:
        admin_conn = psycopg2.connect(admin_url)
        admin_conn.autocommit = True
        try:
            with admin_conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
                    (db_name,),
                )
                cur.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
        finally:
            admin_conn.close()


@contextmanager
def _pool_against(url):
    """Point ``database._connection_pool`` at *url* for the duration."""
    import database

    old_pool = database._connection_pool
    new_pool = psycopg2.pool.ThreadedConnectionPool(
        1, 2, url, cursor_factory=psycopg2.extras.RealDictCursor
    )
    database._connection_pool = new_pool
    try:
        yield
    finally:
        new_pool.closeall()
        database._connection_pool = old_pool


@pytest.mark.integration
def test_create_tables_migrates_a_billing_periods_table_in_its_old_shape():
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("needs a live database")

    import database
    from store.schema import create_tables

    with _temporary_database() as url, _pool_against(url):
        with database.get_connection() as conn, conn.cursor() as cur:
            cur.execute(PRE_MIGRATION_BILLING_PERIODS)
            cur.execute(PRE_MIGRATION_METERING_POINTS)
            cur.execute(PRE_MIGRATION_INVOICES)
            cur.execute(
                """
                INSERT INTO metering_points (metering_point_id)
                VALUES ('CH000000000000000000000000000001')
                """
            )
            cur.execute(
                """
                INSERT INTO billing_periods (community_id, period_start, period_end)
                VALUES (42, TIMESTAMP '2026-01-15 00:00:00',
                            TIMESTAMP '2026-02-15 00:00:00')
                RETURNING id
                """
            )
            period_id = cur.fetchone()["id"]

            assert _column(cur, "billing_periods", "community_id") == "integer"
            assert (
                _column(cur, "billing_periods", "period_start")
                == "timestamp without time zone"
            )
            assert _column(cur, "billing_periods", "input_fingerprint") is None
            assert _column(cur, "billing_periods", "billing_policy_snapshot") is None
            assert _column(cur, "invoices", "participant_id") is None
            assert _column(cur, "invoices", "policy_snapshot") is None
            assert (
                _column(cur, "invoices", "issued_at") == "timestamp without time zone"
            )
            assert _column(cur, "metering_points", "expected_directions") is None

        create_tables()

        with database.get_connection() as conn, conn.cursor() as cur:
            assert (
                _column(cur, "billing_periods", "community_id") == "character varying"
            )
            assert (
                _column(cur, "billing_periods", "period_start")
                == "timestamp with time zone"
            )
            assert (
                _column(cur, "billing_periods", "period_end")
                == "timestamp with time zone"
            )
            # The additive block: a column that exists only inside the CREATE TABLE
            # statement would never have reached this table.
            assert _column(cur, "billing_periods", "input_fingerprint") is not None
            assert (
                _column(cur, "billing_periods", "billing_policy_snapshot") is not None
            )
            for column in (
                "participant_id",
                "policy_snapshot",
                "provenance_snapshot",
                "line_items_snapshot",
                "net_chf",
                "vat_rate_pct",
                "vat_chf",
                "gross_chf",
                "issue_date",
                "due_date",
            ):
                assert _column(cur, "invoices", column) is not None
            assert _column(cur, "invoices", "issued_at") == "timestamp with time zone"
            cur.execute("SELECT indexdef FROM pg_indexes WHERE tablename = 'invoices'")
            invoice_indexes = [row["indexdef"] for row in cur.fetchall()]
            assert any(
                "(billing_period_id, participant_id)" in definition
                for definition in invoice_indexes
            )
            cur.execute(
                """
                SELECT pg_get_triggerdef(oid) AS definition
                FROM pg_trigger
                WHERE tgrelid = 'invoices'::regclass AND NOT tgisinternal
                """
            )
            invoice_triggers = [row["definition"] for row in cur.fetchall()]
            assert any(
                "UPDATE" in definition
                and "DELETE" in definition
                and "invoices" in definition
                for definition in invoice_triggers
            )
            assert _column(cur, "metering_points", "expected_directions") == "ARRAY"
            cur.execute(
                """
                SELECT expected_directions FROM metering_points
                WHERE metering_point_id = 'CH000000000000000000000000000001'
                """
            )
            legacy_point = cur.fetchone()

            cur.execute(
                """
                SELECT community_id, period_start, period_end
                FROM billing_periods WHERE id = %s
                """,
                (period_id,),
            )
            row = cur.fetchone()

        assert (
            database.upsert_metering_points(
                [
                    {
                        "metering_point_id": "CH000000000000000000000000000001",
                        "expected_directions": [
                            "production",
                            "consumption",
                            "production",
                        ],
                    }
                ]
            )
            == 1
        )
        enriched_point = database.get_metering_point("CH000000000000000000000000000001")

    assert row is not None, "the migration must carry the existing row across"
    assert legacy_point["expected_directions"] is None, (
        "the migration must not invent a direction for existing citizen data"
    )
    assert enriched_point["expected_directions"] == ["consumption", "production"]
    assert row["community_id"] == "42", "the integer key becomes its own text"
    # Midnight in Zurich on 15 January is 23:00 UTC the day before.
    assert row["period_start"] == datetime(2026, 1, 14, 23, 0, tzinfo=timezone.utc), (
        "a naive timestamp is read as Europe/Zurich, not as UTC"
    )
    # Midnight in Zurich on 15 February is 23:00 UTC the day before.
    assert row["period_end"] == datetime(2026, 2, 14, 23, 0, tzinfo=timezone.utc), (
        "a naive timestamp is read as Europe/Zurich, not as UTC"
    )


@pytest.mark.integration
def test_issued_invoice_snapshots_are_unique_per_participant_and_immutable():
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("needs a live database")

    import database
    from store.schema import create_tables

    with _temporary_database() as url, _pool_against(url):
        create_tables()
        with database.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO buildings (building_id, email, address, lat, lon)
                VALUES ('building-a', 'a@example.ch', 'Musterweg 1', 47, 8);
                INSERT INTO communities (community_id, name, status)
                VALUES ('LEG-A', 'LEG A', 'active');
                INSERT INTO billing_periods (
                    community_id, period_start, period_end, status,
                    input_fingerprint, source_document_ids, reconciliation,
                    billing_policy_snapshot
                ) VALUES (
                    'LEG-A', %s, %s, 'issued', %s, '["DOC-1"]'::jsonb,
                    '{"difference_kwh": 0}'::jsonb,
                    '{"invoice_prefix":"LEGA","payment_days":30}'::jsonb
                ) RETURNING id
                """,
                (
                    datetime(2026, 1, 1, tzinfo=timezone.utc),
                    datetime(2026, 2, 1, tzinfo=timezone.utc),
                    "a" * 64,
                ),
            )
            period_id = cur.fetchone()["id"]
            cur.execute(
                """
                INSERT INTO invoices (
                    billing_period_id, community_id, participant_id,
                    invoice_number, policy_snapshot, provenance_snapshot,
                    line_items_snapshot, net_chf, vat_rate_pct, vat_chf,
                    gross_chf, issue_date, due_date, status, issued_at
                ) VALUES (
                    %s, 'LEG-A', 'building-a', 'LEGA-2026-000001',
                    '{"delivery_method":"download"}'::jsonb, '{}'::jsonb, '[]'::jsonb,
                    10.01, 8.1, 0.81, 10.82,
                    DATE '2026-02-05', DATE '2026-03-07', 'issued', NOW()
                ) RETURNING id
                """,
                (period_id,),
            )
            invoice_id = cur.fetchone()["id"]

        with pytest.raises(psycopg2.Error):
            with database.get_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE invoices SET gross_chf = 1 WHERE id = %s", (invoice_id,)
                )

        with pytest.raises(psycopg2.Error):
            with database.get_connection() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM invoices WHERE id = %s", (invoice_id,))

        with pytest.raises(psycopg2.IntegrityError):
            with database.get_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO invoices (
                        billing_period_id, community_id, participant_id,
                        invoice_number, policy_snapshot, provenance_snapshot,
                        line_items_snapshot, net_chf, vat_rate_pct, vat_chf,
                        gross_chf, issue_date, due_date, status, issued_at
                    ) SELECT
                        billing_period_id, community_id, participant_id,
                        'LEGA-2026-000002', policy_snapshot, provenance_snapshot,
                        line_items_snapshot, net_chf, vat_rate_pct, vat_chf,
                        gross_chf, issue_date, due_date, status, issued_at
                    FROM invoices WHERE id = %s
                    """,
                    (invoice_id,),
                )

        delivery = database.prepare_invoice_delivery(
            invoice_id, "LEG-A", "building-admin"
        )
        assert delivery["delivery_method"] == "download"
        completed = database.complete_invoice_delivery(
            invoice_id, "LEG-A", "building-admin"
        )
        assert completed["lifecycle_state"] == "delivered"
        events = database.list_invoice_events(invoice_id, "LEG-A")
        assert events[0]["actor_id"] == "building-admin"
        assert events[0]["previous_state"] == "issued"
        assert events[0]["new_state"] == "delivered"

        with pytest.raises(psycopg2.Error):
            with database.get_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE invoice_lifecycle_events SET actor_id = 'changed'"
                    " WHERE invoice_id = %s",
                    (invoice_id,),
                )

        with database.get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('invoice_corrections') AS table_name")
            assert cur.fetchone()["table_name"] == "invoice_corrections"


@pytest.mark.integration
def test_fresh_invoices_schema_stores_issued_at_with_time_zone():
    """A freshly created invoices table records issuance as TIMESTAMPTZ."""
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("needs a live database")

    import database
    from store.schema import create_tables

    with _temporary_database() as url, _pool_against(url):
        create_tables()
        with database.get_connection() as conn, conn.cursor() as cur:
            assert _column(cur, "invoices", "issued_at") == "timestamp with time zone"


@pytest.mark.integration
def test_create_tables_migrates_legacy_invoices_issued_at_to_timestamptz():
    """A naive issued_at is read as UTC (the CONTEXT.md standard), not Zurich."""
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("needs a live database")

    import database
    from store.schema import create_tables

    with _temporary_database() as url, _pool_against(url):
        create_tables()
        with database.get_connection() as conn, conn.cursor() as cur:
            _drop_invoice_schema_for_legacy_fixture(cur)
            cur.execute(PRE_MIGRATION_INVOICES)
            cur.execute(
                """
                INSERT INTO invoices (community_id, invoice_number, issued_at)
                VALUES ('LEG-A', 'LEG-2026-000001', TIMESTAMP '2026-02-05 09:30:00')
                """
            )
            assert (
                _column(cur, "invoices", "issued_at") == "timestamp without time zone"
            )

        create_tables()

        with database.get_connection() as conn, conn.cursor() as cur:
            assert _column(cur, "invoices", "issued_at") == "timestamp with time zone"
            cur.execute(
                "SELECT issued_at FROM invoices"
                " WHERE invoice_number = 'LEG-2026-000001'"
            )
            row = cur.fetchone()

    assert row is not None, "the migration must carry the existing row across"
    # A naive timestamp is already UTC: 09:30 stays 09:30, no one-hour shift.
    assert row["issued_at"] == datetime(2026, 2, 5, 9, 30, tzinfo=timezone.utc), (
        "a naive timestamp is read as UTC, per the CONTEXT.md timestamp standard"
    )


_INVOICE_NUMBERS_DDL = """
    INSERT INTO invoices (community_id, participant_id, invoice_number, status)
    VALUES (%s, %s, %s, %s)
"""


@pytest.mark.integration
def test_invoice_numbers_are_unique_per_community_not_globally():
    """Two LEGs may share a number; one LEG may never issue it twice."""
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("needs a live database")

    import database
    from store.schema import create_tables

    with _temporary_database() as url, _pool_against(url):
        create_tables()
        with database.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                _INVOICE_NUMBERS_DDL,
                ("LEG-A", "building-a", "LEG-2026-000001", "issued"),
            )
            cur.execute(
                _INVOICE_NUMBERS_DDL,
                ("LEG-B", "building-b", "LEG-2026-000001", "issued"),
            )

        with pytest.raises(psycopg2.IntegrityError):
            with database.get_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    _INVOICE_NUMBERS_DDL,
                    ("LEG-A", "building-c", "LEG-2026-000001", "issued"),
                )


@pytest.mark.integration
def test_legacy_global_invoice_number_constraint_is_migrated_away():
    """The pre-#399 global UNIQUE on invoice_number must not survive migration."""
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("needs a live database")

    import database
    from store.schema import create_tables

    with _temporary_database() as url, _pool_against(url):
        create_tables()
        with database.get_connection() as conn, conn.cursor() as cur:
            _drop_invoice_schema_for_legacy_fixture(cur)
            cur.execute(PRE_MIGRATION_INVOICES)

        create_tables()

        with database.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT conname FROM pg_constraint
                WHERE conrelid = 'invoices'::regclass
                  AND conname = 'invoices_invoice_number_key'
                """
            )
            assert cur.fetchone() is None
            cur.execute(
                _INVOICE_NUMBERS_DDL,
                ("LEG-A", "building-a", "LEG-2026-000001", "issued"),
            )
            cur.execute(
                _INVOICE_NUMBERS_DDL,
                ("LEG-B", "building-b", "LEG-2026-000001", "issued"),
            )

        with pytest.raises(psycopg2.IntegrityError):
            with database.get_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    _INVOICE_NUMBERS_DDL,
                    ("LEG-B", "building-d", "LEG-2026-000001", "issued"),
                )


@pytest.mark.integration
def test_only_issued_invoices_are_immutable():
    """Legacy non-issued rows stay mutable and deletable; issued never change."""
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("needs a live database")

    import database
    from store.schema import create_tables

    with _temporary_database() as url, _pool_against(url):
        create_tables()
        with database.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO invoices (community_id, participant_id, invoice_number, status)
                VALUES
                    ('LEG-A', 'building-a', 'LEG-2026-000001', 'draft'),
                    ('LEG-A', 'building-b', 'LEG-2026-000002', 'issued'),
                    ('LEG-A', 'building-c', 'LEG-2026-000003', NULL)
                """
            )

        # Legacy draft rows stay mutable and deletable.
        with database.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE invoices SET total_chf = 12.5 WHERE invoice_number = 'LEG-2026-000001'"
            )
            cur.execute("DELETE FROM invoices WHERE invoice_number = 'LEG-2026-000001'")
        # Legacy rows without a status stay mutable as well.
        with database.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE invoices SET total_chf = 7.5 WHERE invoice_number = 'LEG-2026-000003'"
            )

        # Issued rows reject both verbs.
        with pytest.raises(psycopg2.Error):
            with database.get_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE invoices SET total_chf = 1 WHERE invoice_number = 'LEG-2026-000002'"
                )
        with pytest.raises(psycopg2.Error):
            with database.get_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM invoices WHERE invoice_number = 'LEG-2026-000002'"
                )

        # An issued row cannot be smuggled out via a status change either.
        with pytest.raises(psycopg2.Error):
            with database.get_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE invoices SET status = 'draft' WHERE invoice_number = 'LEG-2026-000002'"
                )


@pytest.mark.integration
def test_unassigned_period_points_are_scoped_by_public_vnb_leg_identifier():
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("needs a live database")

    import database

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 2, 1, tzinfo=timezone.utc)
    with _temporary_database() as url, _pool_against(url):
        with database.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE metering_points (
                    metering_point_id VARCHAR(64) PRIMARY KEY,
                    community_id VARCHAR(64),
                    active BOOLEAN NOT NULL DEFAULT TRUE
                );
                CREATE TABLE sdat_imports (
                    document_id VARCHAR(64) PRIMARY KEY,
                    vnb_community_id VARCHAR(64)
                );
                CREATE TABLE metering_point_readings (
                    metering_point_id VARCHAR(64) NOT NULL,
                    measured_at TIMESTAMPTZ NOT NULL,
                    source_document_id VARCHAR(64)
                )
                """
            )
            cur.execute(
                """
                INSERT INTO metering_points (metering_point_id, community_id)
                VALUES
                    ('POINT-A', 'LEG-A'),
                    ('UNASSIGNED-A', NULL),
                    ('POINT-B', 'LEG-B'),
                    ('UNASSIGNED-B', NULL),
                    ('ASSIGNED-ELSEWHERE', 'LEG-B');
                INSERT INTO sdat_imports (document_id, vnb_community_id)
                VALUES
                    ('DOC-A-OWNED', 'VNB-LEG-A'),
                    ('DOC-A-STRAY', 'VNB-LEG-A'),
                    ('DOC-B-OWNED', 'VNB-LEG-B'),
                    ('DOC-B-STRAY', 'VNB-LEG-B');
                INSERT INTO metering_point_readings (
                    metering_point_id, measured_at, source_document_id
                ) VALUES
                    ('POINT-A', %s, 'DOC-A-OWNED'),
                    ('UNASSIGNED-A', %s, 'DOC-A-STRAY'),
                    ('UNASSIGNED-A', %s, 'DOC-A-STRAY'),
                    ('POINT-B', %s, 'DOC-B-OWNED'),
                    ('UNASSIGNED-B', %s, 'DOC-B-STRAY'),
                    ('ASSIGNED-ELSEWHERE', %s, 'DOC-A-STRAY')
                """,
                (
                    start,
                    start,
                    start + timedelta(minutes=15),
                    start,
                    start,
                    start,
                ),
            )

        found = database.get_unassigned_period_metering_point_ids("LEG-A", start, end)

    assert found == ["UNASSIGNED-A"]
    assert "UNASSIGNED-B" not in found, "another LEG's point ID must stay private"
    assert "ASSIGNED-ELSEWHERE" not in found


PRE_MIGRATION_BILLING_TARIFFS = """
    CREATE TABLE billing_tariffs (
        id SERIAL PRIMARY KEY,
        community_id VARCHAR(64) NOT NULL REFERENCES communities(community_id),
        effective_from TIMESTAMPTZ NOT NULL,
        effective_to TIMESTAMPTZ,
        internal_price_chf_per_kwh DECIMAL(12, 6) NOT NULL,
        grid_fee_chf_per_kwh DECIMAL(12, 6) NOT NULL,
        network_level VARCHAR(16) NOT NULL,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(community_id, effective_from)
    )
"""


def _complete_policy(effective_from):
    return {
        "effective_from": effective_from,
        "internal_price_chf_per_kwh": Decimal("0.15"),
        "grid_fee_chf_per_kwh": Decimal("0.08"),
        "network_level": "same",
        "distribution_model": "proportional",
        "vat_mode": "none",
        "vat_rate_pct": Decimal(0),
        "payment_days": 30,
        "invoice_prefix": "LEG-2026",
        "delivery_method": "email",
    }


@pytest.mark.integration
def test_billing_tariffs_migration_keeps_legacy_rows_incomplete():
    """The additive policy columns must not invent values for existing rows."""
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("needs a live database")

    import database
    from store.schema import create_tables

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 2, 1, tzinfo=timezone.utc)

    with _temporary_database() as url, _pool_against(url):
        # Start from the complete current schema so unrelated tables and indexes
        # remain valid, then replace only the table whose old shape we exercise.
        create_tables()
        with database.get_connection() as conn, conn.cursor() as cur:
            cur.execute("DROP TABLE billing_tariffs")
            cur.execute(PRE_MIGRATION_BILLING_TARIFFS)
            cur.execute("""
                INSERT INTO communities (community_id, name, status)
                VALUES ('LEG-A', 'LEG A', 'active')
            """)
            cur.execute(
                """
                INSERT INTO billing_tariffs
                    (community_id, effective_from, internal_price_chf_per_kwh,
                     grid_fee_chf_per_kwh, network_level)
                VALUES ('LEG-A', %s, 0.15, 0.08, 'same')
                """,
                (start,),
            )
            assert _column(cur, "billing_tariffs", "vat_mode") is None

        create_tables()

        with database.get_connection() as conn, conn.cursor() as cur:
            for column in (
                "distribution_model",
                "vat_mode",
                "vat_rate_pct",
                "payment_days",
                "invoice_prefix",
                "delivery_method",
            ):
                assert _column(cur, "billing_tariffs", column) is not None
            cur.execute(
                """
                SELECT vat_mode, vat_rate_pct, payment_days, invoice_prefix,
                       delivery_method
                FROM billing_tariffs WHERE community_id = 'LEG-A'
                """
            )
            legacy = cur.fetchone()

        legacy_policy = database.get_billing_policy("LEG-A", start, end)

    assert legacy == {
        "vat_mode": None,
        "vat_rate_pct": None,
        "payment_days": None,
        "invoice_prefix": None,
        "delivery_method": None,
    }, "the migration must not invent policy values for legacy rows"
    assert legacy_policy is None, "an incomplete legacy row is not a resolvable policy"


@pytest.mark.integration
def test_get_billing_policy_fails_closed_across_a_policy_boundary_live():
    """Executable proof: a newer version inside the period refuses the period."""
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("needs a live database")

    import database
    from store.schema import create_tables

    january = datetime(2026, 1, 1, tzinfo=timezone.utc)
    february = datetime(2026, 2, 1, tzinfo=timezone.utc)
    mid_january = datetime(2026, 1, 15, tzinfo=timezone.utc)

    with _temporary_database() as url, _pool_against(url):
        create_tables()
        with database.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO communities (community_id, name, status)
                VALUES ('LEG-A', 'LEG A', 'active')
                """
            )
        database.save_billing_policy("LEG-A", _complete_policy(january))
        database.save_billing_policy("LEG-A", _complete_policy(mid_january))

        covering = database.get_billing_policy("LEG-A", mid_january, february)
        split = database.get_billing_policy("LEG-A", january, february)
        before_boundary = database.get_billing_policy("LEG-A", january, mid_january)

    assert covering is not None
    assert covering["effective_from"] == mid_january
    assert split is None, "a policy boundary inside the period must fail closed"
    assert before_boundary is not None
    assert before_boundary["effective_from"] == january


@pytest.mark.integration
def test_get_billing_policy_fails_closed_on_newest_incomplete_version():
    """The newest version at/before period_start must be complete; no fallback."""
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("needs a live database")

    import database
    from store.schema import create_tables

    december = datetime(2025, 12, 1, tzinfo=timezone.utc)
    january = datetime(2026, 1, 1, tzinfo=timezone.utc)
    february = datetime(2026, 2, 1, tzinfo=timezone.utc)

    with _temporary_database() as url, _pool_against(url):
        create_tables()
        with database.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO communities (community_id, name, status)
                VALUES ('LEG-A', 'LEG A', 'active')
                """
            )
        # Older complete version, then newer incomplete version.
        old = _complete_policy(december)
        database.save_billing_policy("LEG-A", old)
        incomplete = {
            "effective_from": january,
            "internal_price_chf_per_kwh": Decimal("0.15"),
            "grid_fee_chf_per_kwh": Decimal("0.08"),
            "network_level": "same",
            # missing distribution_model and other versioned fields
        }
        with database.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO billing_tariffs
                    (community_id, effective_from, internal_price_chf_per_kwh,
                     grid_fee_chf_per_kwh, network_level)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    "LEG-A",
                    incomplete["effective_from"],
                    incomplete["internal_price_chf_per_kwh"],
                    incomplete["grid_fee_chf_per_kwh"],
                    incomplete["network_level"],
                ),
            )

        resolved = database.get_billing_policy("LEG-A", january, february)

    assert resolved is None, "newest incomplete version must not fall back to old"


@pytest.mark.integration
def test_get_billing_policy_fails_closed_on_newest_expired_version():
    """The newest version at/before period_start must cover the period; no fallback."""
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("needs a live database")

    import database
    from store.schema import create_tables

    december = datetime(2025, 12, 1, tzinfo=timezone.utc)
    mid_december = datetime(2025, 12, 15, tzinfo=timezone.utc)
    late_december = datetime(2025, 12, 20, tzinfo=timezone.utc)
    january = datetime(2026, 1, 1, tzinfo=timezone.utc)
    february = datetime(2026, 2, 1, tzinfo=timezone.utc)

    with _temporary_database() as url, _pool_against(url):
        create_tables()
        with database.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO communities (community_id, name, status)
                VALUES ('LEG-A', 'LEG A', 'active')
                """
            )
        old = _complete_policy(december)
        database.save_billing_policy("LEG-A", old)
        expired = _complete_policy(mid_december)
        database.save_billing_policy("LEG-A", expired)
        with database.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE billing_tariffs SET effective_to = %s
                WHERE community_id = 'LEG-A' AND effective_from = %s
                """,
                (late_december, mid_december),
            )

        resolved = database.get_billing_policy("LEG-A", january, february)

    assert resolved is None, "newest expired version must not fall back to old"


@pytest.mark.integration
def test_billing_policy_effective_date_matches_zurich_boundary():
    """A form date resolves to Europe/Zurich midnight and matches period boundaries."""
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("needs a live database")

    import database
    from store.schema import create_tables

    with _temporary_database() as url, _pool_against(url):
        create_tables()
        with database.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO communities (community_id, name, status)
                VALUES ('LEG-A', 'LEG A', 'active')
                """
            )
        form_policy = billing_policy.validate_policy_form(
            {
                "effective_from": "2026-01-01",
                "internal_price_rp": "15.00",
                "grid_fee_rp": "8.00",
                "network_level": "same",
                "distribution_model": "proportional",
                "vat_mode": "none",
                "vat_rate_pct": "",
                "payment_days": "30",
                "invoice_prefix": "LEG-2026",
                "delivery_method": "email",
            }
        )["policy"]
        database.save_billing_policy("LEG-A", form_policy)

        period_end = datetime(2026, 2, 1, tzinfo=ZoneInfo("Europe/Zurich"))
        resolved = database.get_billing_policy(
            "LEG-A", form_policy["effective_from"], period_end
        )

    assert resolved is not None
    assert resolved["effective_from"] == form_policy["effective_from"]
    assert resolved["effective_from"].astimezone(
        ZoneInfo("Europe/Zurich")
    ).utcoffset() == timedelta(hours=1)


@pytest.mark.integration
def test_billing_tariffs_check_constraints_are_installed_idempotently():
    """Pre-migration table gets constraints; running create_tables again is safe."""
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("needs a live database")

    import database
    from store.schema import create_tables

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    with _temporary_database() as url, _pool_against(url):
        create_tables()
        with database.get_connection() as conn, conn.cursor() as cur:
            cur.execute("DROP TABLE billing_tariffs")
            cur.execute(PRE_MIGRATION_BILLING_TARIFFS)
            cur.execute(
                """
                INSERT INTO communities (community_id, name, status)
                VALUES ('LEG-A', 'LEG A', 'active')
                """
            )
            cur.execute(
                """
                INSERT INTO billing_tariffs
                    (community_id, effective_from, internal_price_chf_per_kwh,
                     grid_fee_chf_per_kwh, network_level)
                VALUES ('LEG-A', %s, 0.15, 0.08, 'same')
                """,
                (start,),
            )

        create_tables()
        create_tables()  # idempotency

        with database.get_connection() as conn, conn.cursor() as cur:
            for constraint in (
                "chk_billing_tariffs_distribution_model",
                "chk_billing_tariffs_vat_mode",
                "chk_billing_tariffs_vat_rate",
                "chk_billing_tariffs_payment_days",
                "chk_billing_tariffs_invoice_prefix",
                "chk_billing_tariffs_delivery_method",
            ):
                assert _constraint(cur, "billing_tariffs", constraint) is not None
            cur.execute(
                """
                SELECT distribution_model, vat_mode, vat_rate_pct, payment_days,
                       invoice_prefix, delivery_method
                FROM billing_tariffs WHERE community_id = 'LEG-A'
                """
            )
            legacy = cur.fetchone()

    assert all(v is None for v in legacy.values()), "legacy NULL row must survive"


@pytest.mark.integration
def test_billing_tariffs_nullable_check_constraints_reject_invalid_values():
    """CHECK constraints allow NULL legacy rows but refuse invalid non-NULL data."""
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("needs a live database")

    import database
    from store.schema import create_tables

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    with _temporary_database() as url, _pool_against(url):
        create_tables()
        with database.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO communities (community_id, name, status)
                VALUES ('LEG-A', 'LEG A', 'active')
                """
            )
            # Legacy NULL row must survive.
            cur.execute(
                """
                INSERT INTO billing_tariffs
                    (community_id, effective_from, internal_price_chf_per_kwh,
                     grid_fee_chf_per_kwh, network_level)
                VALUES ('LEG-A', %s, 0.15, 0.08, 'same')
                """,
                (start,),
            )

        # Single-field invalid updates on a legacy NULL row must fail.
        invalid_cases = [
            ("distribution_model", "'simple'"),
            ("vat_mode", "'reduced'"),
            ("vat_rate_pct", "101"),
            ("vat_rate_pct", "-1"),
            ("payment_days", "0"),
            ("payment_days", "366"),
            ("invoice_prefix", "'lowercase'"),
            ("invoice_prefix", "'A'"),
            ("delivery_method", "'post'"),
        ]
        for column, value in invalid_cases:
            with pytest.raises(psycopg2.Error):
                with database.get_connection() as conn, conn.cursor() as cur:
                    cur.execute(
                        f"""
                        UPDATE billing_tariffs
                        SET {column} = {value}
                        WHERE community_id = 'LEG-A'
                        """
                    )

        # Valid vat pairs must be accepted.
        with database.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE billing_tariffs
                SET vat_mode = 'none', vat_rate_pct = 0
                WHERE community_id = 'LEG-A'
                """
            )
            cur.execute(
                """
                UPDATE billing_tariffs
                SET vat_mode = 'standard', vat_rate_pct = 8.1
                WHERE community_id = 'LEG-A'
                """
            )

        with database.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT distribution_model, vat_mode, vat_rate_pct, payment_days,
                       invoice_prefix, delivery_method
                FROM billing_tariffs WHERE community_id = 'LEG-A'
                """
            )
            legacy = cur.fetchone()

    assert legacy["vat_mode"] == "standard"
    assert legacy["vat_rate_pct"] == Decimal("8.10")
    # Other columns stayed NULL because each invalid update rolled back.
    assert all(
        legacy[c] is None
        for c in (
            "distribution_model",
            "payment_days",
            "invoice_prefix",
            "delivery_method",
        )
    )
