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
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import psycopg2.extras
import psycopg2.pool
import pytest

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
