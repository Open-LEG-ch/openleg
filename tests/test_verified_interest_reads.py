# SPDX-License-Identifier: AGPL-3.0-or-later
"""Consistent household selection across verified-interest readers."""

import json

import pytest

import database as db
from tests import test_interest_postgres

interest_database = test_interest_postgres.interest_database


def _seed(
    source, identifier, email, *, bfs=2554, roles=(), solar=False, days=1, verified=True
):
    timestamp = (
        "NULL" if days is None else "CURRENT_TIMESTAMP - (%s * INTERVAL '1 day')"
    )
    params = [identifier, email, bfs, json.dumps(roles), solar, verified]
    if days is not None:
        params.append(days)
    with db.get_connection() as conn, conn.cursor() as cur:
        if source == "building":
            cur.execute(
                f"""INSERT INTO buildings
                (building_id, email, bfs_number, roles, has_solar, verified,
                 registered_at, address, lat, lon)
                VALUES (%s, %s, %s, %s, %s, %s, {timestamp}, 'Testweg', 47.2, 8.2)""",
                params,
            )
        else:
            cur.execute(
                f"""INSERT INTO coverage_requests
                (request_id, email, bfs_number, roles, has_solar, verified,
                 created_at, plz, municipality_name, token_expires_at)
                VALUES (%s, %s, %s, %s, %s, %s, {timestamp}, '4533', 'Riedholz',
                        CURRENT_TIMESTAMP + INTERVAL '30 days')""",
                params,
            )


@pytest.mark.integration
def test_readers_share_source_priority_and_newest_household_details(interest_database):
    _seed("building", "a-old", "ONE@example.ch", roles=["owner"], solar=True, days=50)
    _seed("building", "b-new", "one@example.ch", roles=["tenant"], days=2)
    _seed("building", "c-tie", "one@example.ch", roles=["local_business"], days=2)
    # Pin an exact tie, independent of separate seed transaction timestamps.
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE buildings SET registered_at = (SELECT registered_at FROM buildings WHERE building_id = 'b-new') WHERE building_id = 'c-tie'"
        )
    _seed("building", "d-undated", "one@example.ch", roles=["owner"], days=None)
    _seed("coverage", "e", "one@example.ch", roles=["solar_producer"], solar=True)
    _seed("coverage", "f-old", "TWO@example.ch", roles=["owner"], days=40)
    _seed("coverage", "g-new", "two@example.ch", roles=["solar_producer"], solar=True)
    _seed("building", "h", "hidden@example.ch", verified=False)
    _seed("coverage", "i", "hidden-coverage@example.ch", verified=False)
    _seed("building", "j", "unplaced@example.ch", bfs=None)
    _seed("coverage", "k", "unplaced-coverage@example.ch", bfs=None)
    _seed("building", "l", "one@example.ch", bfs=999)

    assert db.get_interest_counts_by_bfs() == {2554: 2, 999: 1}
    assert len(db.get_operator_interest_records()) == 12
    assert db.get_verified_interest_recipients(
        2554, exclude_email="ONE@example.ch"
    ) == ["two@example.ch"]
    assert db.get_municipality_interest_summary(2554) == {
        "verified_total": 2,
        "last_30_days": 2,
        "has_solar": 1,
        "address_problems": 1,
        "roles": {"tenant": 1, "solar_producer": 1},
    }


@pytest.mark.integration
def test_scoped_count_and_repeated_schema_keep_municipality_identity(interest_database):
    _seed("building", "a", "one@example.ch")
    _seed("coverage", "b", "ONE@example.ch")
    _seed("building", "c", "two@example.ch", bfs=999)
    db.create_tables()
    db.create_tables()
    assert db.get_interest_count(2554) == 1
    assert db.get_interest_count(999) == 1
    assert db.get_interest_count(1234) is None


@pytest.mark.integration
def test_reader_failures_keep_existing_empty_fallbacks(interest_database):
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("DROP VIEW verified_interest")
    assert db.get_interest_count(2554) is None
    assert db.get_interest_counts_by_bfs() == {}
    assert db.get_verified_interest_recipients(2554) == []
    assert db.get_municipality_interest_summary(2554) == {}
