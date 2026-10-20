# SPDX-License-Identifier: AGPL-3.0-or-later
"""Keep the prior application's schema initialization able to start."""

import pytest

from tests import test_interest_postgres

interest_database = test_interest_postgres.interest_database

pytestmark = pytest.mark.integration

# Exact seven-column statement from release v0.1.21, source 8cb49fe.
LEGACY_VIEW_SQL = """
CREATE OR REPLACE VIEW verified_interest AS
SELECT DISTINCT ON (bfs_number, LOWER(email))
       bfs_number, LOWER(email) AS email, roles, has_solar,
       created_at, address_problem, source_id
FROM (
    SELECT bfs_number, email, roles, has_solar,
           registered_at AS created_at,
           FALSE AS address_problem, 1 AS source_priority,
           building_id AS source_id
    FROM buildings
    WHERE verified = TRUE AND bfs_number IS NOT NULL
    UNION ALL
    SELECT bfs_number, email, roles, has_solar, created_at,
           TRUE AS address_problem, 2 AS source_priority,
           request_id AS source_id
    FROM coverage_requests
    WHERE verified = TRUE AND bfs_number IS NOT NULL
) interest
ORDER BY bfs_number, LOWER(email), source_priority,
         created_at DESC NULLS LAST, source_id
"""


def test_prior_view_initialization_preserves_saved_recipient_languages(
    interest_database,
):
    db = interest_database
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO buildings
                (building_id,email,address,lat,lon,verified,bfs_number,preferred_locale)
            VALUES
                ('collision','profile@example.invalid','Synthetic 1',47,8,TRUE,261,'fr_CH'),
                ('winner','WINNER@example.invalid','Synthetic 2',47,8,TRUE,261,'de_CH'),
                ('hidden','hidden@example.invalid','Synthetic 3',47,8,TRUE,261,'fr_CH')
        """)
        cur.execute("""
            INSERT INTO consents(building_id,updates_opt_in)
            VALUES ('collision',TRUE),('winner',TRUE),('hidden',FALSE)
        """)
        cur.execute("""
            INSERT INTO coverage_requests
                (request_id,email,plz,municipality_name,verified,bfs_number,
                 preferred_locale,token_expires_at)
            VALUES
                ('collision','coverage@example.invalid','8000','Synthetic',TRUE,261,'de_CH',NOW()),
                ('other','winner@example.invalid','8000','Synthetic',TRUE,261,'fr_CH',NOW())
        """)
    db.create_tables()
    db.create_tables()
    # This is executed by the prior application's real initialization.
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(LEGACY_VIEW_SQL)
    assert db.get_verified_interest_recipient_preferences(261) == [
        {"email": "coverage@example.invalid", "preferred_locale": "de_CH"},
        {"email": "profile@example.invalid", "preferred_locale": "fr_CH"},
        {"email": "winner@example.invalid", "preferred_locale": "de_CH"},
    ]
    assert db.get_verified_interest_recipient_preferences(
        261, "PROFILE@example.invalid"
    ) == [
        {"email": "coverage@example.invalid", "preferred_locale": "de_CH"},
        {"email": "winner@example.invalid", "preferred_locale": "de_CH"},
    ]


@pytest.mark.parametrize(
    "index_shape", ["legacy_full", "current_partial", "legacy_partial"]
)
def test_prior_index_initialization_accepts_a_corrected_window(
    interest_database, index_shape
):
    db = interest_database
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO communities(community_id,name) VALUES('synthetic-index','Synthetic community')"
        )
        cur.execute("""
            INSERT INTO billing_periods(community_id,period_start,period_end,status)
            VALUES('synthetic-index','2026-09-01Z','2026-10-01Z','invalidated')
        """)
        cur.execute("DROP INDEX IF EXISTS idx_billing_period_live_window")
        cur.execute("DROP INDEX IF EXISTS uq_billing_period_community_window")
        index_name = (
            "idx_billing_period_live_window"
            if index_shape == "current_partial"
            else "uq_billing_period_community_window"
        )
        predicate = (
            ""
            if index_shape == "legacy_full"
            else " WHERE status IS DISTINCT FROM 'invalidated'"
        )
        cur.execute(
            "CREATE UNIQUE INDEX "
            + index_name
            + " ON billing_periods(community_id,period_start,period_end)"
            + predicate
        )
    db.create_tables()
    db.create_tables()
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO billing_periods(community_id,period_start,period_end,status)
            VALUES('synthetic-index','2026-09-01Z','2026-10-01Z','draft')
        """)
        # Exact index statement issued during v0.1.21 initialization.
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_billing_period_community_window ON billing_periods(community_id,period_start,period_end)"
        )
        cur.execute(
            "SELECT count(*) AS count FROM billing_periods WHERE community_id='synthetic-index'"
        )
        assert cur.fetchone()["count"] == 2
        cur.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename='billing_periods' AND indexname IN ('uq_billing_period_community_window','idx_billing_period_live_window')"
        )
        assert [r["indexname"] for r in cur.fetchall()] == [
            "uq_billing_period_community_window"
        ]
    import psycopg2

    with pytest.raises(psycopg2.errors.UniqueViolation):
        with db.get_connection() as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO billing_periods(community_id,period_start,period_end,status)
                VALUES('synthetic-index','2026-09-01Z','2026-10-01Z','draft')
            """)
