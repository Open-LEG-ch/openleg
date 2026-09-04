# SPDX-License-Identifier: AGPL-3.0-or-later
"""PostgreSQL behaviour contracts for store.ops.get_ops_snapshots filters."""

import json
import os
import secrets
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import psycopg2.extras
import psycopg2.pool
import pytest

import database

T1 = datetime(2026, 6, 14, 10, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 6, 14, 10, 3, tzinfo=timezone.utc)
T3 = datetime(2026, 6, 14, 10, 5, tzinfo=timezone.utc)


@contextmanager
def _temporary_database():
    original_url = os.environ["DATABASE_URL"]
    parsed = urlsplit(original_url)
    admin_url = urlunsplit(parsed._replace(path="/postgres"))
    db_name = f"openleg_ops_{secrets.token_hex(6)}"
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


def _seed(source, category, created_at, status="ok", summary="", payload=None):
    with database.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ops_snapshots
                (source, category, status, summary_text, payload, created_at)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s)
            """,
            (source, category, status, summary, json.dumps(payload or {}), created_at),
        )


def _require_database_url():
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("needs a live database")


@pytest.mark.integration
def test_no_filter_returns_all_snapshots_newest_first():
    _require_database_url()

    with _temporary_database() as url, _pool_against(url):
        database.create_tables()
        _seed("openclaw", "openclaw_health", T1)
        _seed("agentmail", "lea_inbox", T3, summary="newest")
        _seed("sdat", "sdat_import", T2)

        rows = database.get_ops_snapshots()

    assert [(r["source"], r["category"]) for r in rows] == [
        ("agentmail", "lea_inbox"),
        ("sdat", "sdat_import"),
        ("openclaw", "openclaw_health"),
    ]
    assert [r["created_at"] for r in rows] == sorted(
        (r["created_at"] for r in rows), reverse=True
    )


@pytest.mark.integration
def test_source_filter_returns_only_that_source():
    _require_database_url()

    with _temporary_database() as url, _pool_against(url):
        database.create_tables()
        _seed("openclaw", "openclaw_health", T1)
        _seed("openclaw", "openclaw_sessions", T2, summary="wanted")
        _seed("agentmail", "lea_inbox", T3)

        rows = database.get_ops_snapshots(source="openclaw")

    assert len(rows) == 2
    assert {r["source"] for r in rows} == {"openclaw"}
    assert [(r["category"], r["created_at"]) for r in rows] == [
        ("openclaw_sessions", T2),
        ("openclaw_health", T1),
    ]


@pytest.mark.integration
def test_category_filter_returns_only_that_category():
    _require_database_url()

    with _temporary_database() as url, _pool_against(url):
        database.create_tables()
        _seed("openclaw", "openclaw_health", T1)
        _seed("agentmail", "lea_inbox", T2, summary="wanted-a")
        _seed("sdat", "lea_inbox", T3, summary="wanted-b")

        rows = database.get_ops_snapshots(category="lea_inbox")

    assert len(rows) == 2
    assert {r["category"] for r in rows} == {"lea_inbox"}
    assert [(r["source"], r["created_at"]) for r in rows] == [
        ("sdat", T3),
        ("agentmail", T2),
    ]


@pytest.mark.integration
def test_combined_source_and_category_filters_do_not_leak():
    _require_database_url()

    with _temporary_database() as url, _pool_against(url):
        database.create_tables()
        _seed("openclaw", "openclaw_health", T1, summary="both-match")
        _seed("openclaw", "openclaw_sessions", T2, summary="source-only")
        _seed("agentmail", "openclaw_health", T3, summary="category-only")
        _seed("agentmail", "lea_inbox", T1, summary="neither")

        rows = database.get_ops_snapshots(source="openclaw", category="openclaw_health")

    assert len(rows) == 1
    assert rows[0]["source"] == "openclaw"
    assert rows[0]["category"] == "openclaw_health"
    assert rows[0]["summary_text"] == "both-match"


@pytest.mark.integration
def test_limit_returns_only_the_newest_rows():
    _require_database_url()

    with _temporary_database() as url, _pool_against(url):
        database.create_tables()
        _seed("openclaw", "openclaw_health", T1, summary="oldest")
        _seed("openclaw", "openclaw_health", T2, summary="middle")
        _seed("openclaw", "openclaw_health", T3, summary="newest")

        rows = database.get_ops_snapshots(limit=2)

    assert [r["summary_text"] for r in rows] == ["newest", "middle"]


@pytest.mark.integration
def test_read_failure_returns_empty_list():
    _require_database_url()

    with _temporary_database() as url, _pool_against(url):
        # No schema created: the read against the missing table must degrade
        # to an empty list instead of raising.
        assert database.get_ops_snapshots() == []
