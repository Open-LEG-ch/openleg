# SPDX-License-Identifier: AGPL-3.0-or-later
"""PostgreSQL behaviour contracts for metering writes and the SDAT ledger."""

import os
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import psycopg2.extras
import psycopg2.pool
import pytest

import database

POINT = "CH000000000000000000000000000001"
MEASURED_AT = datetime(2026, 1, 5, 23, 0, tzinfo=timezone.utc)

METERING_SCHEMA = """
    CREATE TABLE metering_points (
        metering_point_id VARCHAR(64) PRIMARY KEY
    );
    CREATE TABLE metering_point_readings (
        id BIGSERIAL PRIMARY KEY,
        metering_point_id VARCHAR(64) NOT NULL
            REFERENCES metering_points(metering_point_id),
        direction VARCHAR(16) NOT NULL,
        measured_at TIMESTAMPTZ NOT NULL,
        resolution_minutes SMALLINT NOT NULL,
        total_kwh NUMERIC(12, 4),
        grid_kwh NUMERIC(12, 4),
        community_kwh NUMERIC(12, 4),
        condition_code VARCHAR(8),
        source_document_id VARCHAR(64),
        imported_at TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE (metering_point_id, direction, measured_at)
    );
    CREATE TABLE sdat_imports (
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
"""


@contextmanager
def _temporary_database():
    original_url = os.environ["DATABASE_URL"]
    parsed = urlsplit(original_url)
    admin_url = urlunsplit(parsed._replace(path="/postgres"))
    db_name = f"openleg_metering_{secrets.token_hex(6)}"
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
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s",
                    (db_name,),
                )
                cur.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
        finally:
            admin_conn.close()


@contextmanager
def _pool_against(url):
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


def _reading(sequence, *, community_kwh="0.040"):
    return {
        "metering_point_id": POINT,
        "direction": "consumption",
        "measured_at": MEASURED_AT + timedelta(minutes=15 * (sequence - 1)),
        "resolution_minutes": 15,
        "total_kwh": Decimal("0.100"),
        "grid_kwh": Decimal("0.060"),
        "community_kwh": Decimal(community_kwh),
        "condition_code": None,
    }


def _setup_schema():
    with database.get_connection() as conn, conn.cursor() as cur:
        cur.execute(METERING_SCHEMA)


@pytest.mark.integration
def test_reading_counts_and_corrections_are_transaction_visible():
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("needs a live database")

    with _temporary_database() as url, _pool_against(url):
        _setup_schema()
        pristine = [_reading(1), _reading(2)]

        created = database.save_metering_point_readings(
            pristine, source_document_id="DOC-1"
        )
        assert created == {
            "written": 2,
            "new": 2,
            "corrected": 0,
            "unchanged": 0,
            "samples": [],
        }
        assert len(database.get_metering_point_readings(POINT)) == 2

        unchanged = database.save_metering_point_readings(
            pristine, source_document_id="DOC-1"
        )
        assert unchanged == {
            "written": 2,
            "new": 0,
            "corrected": 0,
            "unchanged": 2,
            "samples": [],
        }

        corrected_rows = [_reading(1, community_kwh="0.041"), _reading(2)]
        corrected = database.save_metering_point_readings(
            corrected_rows, source_document_id="DOC-1"
        )
        assert corrected == {
            "written": 2,
            "new": 0,
            "corrected": 1,
            "unchanged": 1,
            "samples": [(POINT, "consumption", MEASURED_AT)],
        }

        provenance_corrected = database.save_metering_point_readings(
            corrected_rows, source_document_id="DOC-2"
        )
        assert {
            key: value
            for key, value in provenance_corrected.items()
            if key != "samples"
        } == {
            "written": 2,
            "new": 0,
            "corrected": 2,
            "unchanged": 0,
        }
        assert set(provenance_corrected["samples"]) == {
            (POINT, "consumption", MEASURED_AT),
            (POINT, "consumption", MEASURED_AT + timedelta(minutes=15)),
        }

        stored = database.get_metering_point_readings(POINT)

    assert stored[0]["source_document_id"] == "DOC-2"
    assert stored[1]["source_document_id"] == "DOC-2"
    by_time = {row["measured_at"]: row for row in stored}
    assert by_time[MEASURED_AT]["community_kwh"] == pytest.approx(0.041)
    assert by_time[MEASURED_AT + timedelta(minutes=15)][
        "community_kwh"
    ] == pytest.approx(0.04)


@pytest.mark.integration
def test_sdat_ledger_conflict_updates_audit_fields_without_replacing_identity():
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("needs a live database")

    created_at = datetime(2026, 1, 6, 1, 0, tzinfo=timezone.utc)
    period_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    period_end = datetime(2026, 1, 6, tzinfo=timezone.utc)
    original = {
        "document_id": "DOC-1",
        "doc_type": "E66",
        "file_name": "first.xml",
        "vnb_community_id": "VNB-LEG-1",
        "document_created_at": created_at,
        "period_start": period_start,
        "period_end": period_end,
        "block_count": 2,
        "row_count": 6,
        "new_count": 6,
        "corrected_count": 0,
    }

    with _temporary_database() as url, _pool_against(url):
        _setup_schema()
        assert database.record_sdat_import(original) is True
        first = database.get_sdat_import("DOC-1")

        replacement = {
            **original,
            "doc_type": "E99",
            "file_name": "second.xml.gz",
            "vnb_community_id": "VNB-OTHER",
            "block_count": 3,
            "row_count": 9,
            "new_count": 1,
            "corrected_count": 8,
        }
        assert database.record_sdat_import(replacement) is True
        updated = database.get_sdat_import("DOC-1")

    assert updated["id"] == first["id"], "the conflict updates one ledger row"
    assert updated["file_name"] == "second.xml.gz"
    assert updated["block_count"] == 3
    assert updated["row_count"] == 9
    assert updated["new_count"] == 1
    assert updated["corrected_count"] == 8
    assert updated["doc_type"] == "E66"
    assert updated["vnb_community_id"] == "VNB-LEG-1"
    assert updated["document_created_at"] == created_at
    assert updated["period_start"] == period_start
    assert updated["period_end"] == period_end
