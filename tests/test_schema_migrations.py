# SPDX-License-Identifier: AGPL-3.0-or-later
"""The guarded migrations, executed against a table in its old shape.

`store/schema.py` carries hand-written migrations, each behind an
`information_schema` existence check. Nothing had ever run one: the schema tests
fake the cursor or match the migration SQL as source text, and the only
database-backed test in the suite runs against a freshly created container,
where every existence check evaluates against a database that never had the old
shape. The `ALTER` branches were unreachable by anything in this repository.

Two structural traps follow from the design, and this test is the only thing
that would notice either one:

- a column added only inside a `CREATE TABLE IF NOT EXISTS` block never reaches
  a deployed table, because Postgres skips the whole statement once the table
  exists
- a type change needs its own guarded `ALTER`, written by hand, per column

Marked `integration`, so it runs in CI against the disposable `postgres:16`
service and skips everywhere else. It rebuilds `billing_periods` and the two
tables that reference it, which `create_tables()` restores as it goes.
"""

import os
from datetime import datetime, timezone

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


@pytest.mark.integration
def test_create_tables_migrates_a_billing_periods_table_in_its_old_shape():
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("needs a live database")

    import database
    from store.schema import create_tables

    assert database.init_db(), "the pool must come up before the migration runs"

    with database.get_connection() as conn, conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS invoices CASCADE")
        cur.execute("DROP TABLE IF EXISTS billing_line_items CASCADE")
        cur.execute("DROP TABLE IF EXISTS billing_periods CASCADE")
        cur.execute(PRE_MIGRATION_BILLING_PERIODS)
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

    create_tables()

    with database.get_connection() as conn, conn.cursor() as cur:
        assert _column(cur, "billing_periods", "community_id") == "character varying"
        assert (
            _column(cur, "billing_periods", "period_start")
            == "timestamp with time zone"
        )
        # The additive block: a column that exists only inside the CREATE TABLE
        # statement would never have reached this table.
        assert _column(cur, "billing_periods", "input_fingerprint") is not None

        cur.execute(
            """
            SELECT community_id, period_start
            FROM billing_periods WHERE id = %s
            """,
            (period_id,),
        )
        row = cur.fetchone()

    assert row is not None, "the migration must carry the existing row across"
    assert row["community_id"] == "42", "the integer key becomes its own text"
    # Midnight in Zurich on 15 January is 23:00 UTC the day before.
    assert row["period_start"] == datetime(2026, 1, 14, 23, 0, tzinfo=timezone.utc), (
        "a naive timestamp is read as Europe/Zurich, not as UTC"
    )
