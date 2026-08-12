# SPDX-License-Identifier: AGPL-3.0-or-later
"""Interface tests for the schema repository (store.schema).

The table and index DDL used to sit inline in `database.py`, which made the
connection seam module the largest file in the repository. The DDL moves to
`store.schema`, resolves the seam via `database.get_connection` like every
other store module, and `database` keeps delegating so callers of
`database.init_db()` are unaffected.
"""

import os
from contextlib import contextmanager

import database
from store import schema

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Tables every deployment must get from a fresh create_tables() run.
CORE_TABLES = (
    "buildings",
    "clusters",
    "tokens",
    "dashboard_access_tokens",
    "scheduled_emails",
    "municipality_profiles",
    "elcom_tariffs",
    "communities",
    "billing_periods",
    "utility_clients",
    "white_label_configs",
    "meter_readings",
    "leg_registry",
    "lea_reports",
    "ops_snapshots",
)


class _FakeCursor:
    def __init__(self):
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append(query)

    def fetchall(self):
        return []

    def fetchone(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def _conn_ctx(cursor):
    @contextmanager
    def _factory():
        yield _FakeConnection(cursor)

    return _factory


def _run(monkeypatch, target):
    cursor = _FakeCursor()
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cursor))
    target()
    return "\n".join(cursor.executed).lower()


class TestSchemaModule:
    def test_create_tables_issues_the_core_ddl(self, monkeypatch):
        executed = _run(monkeypatch, schema.create_tables)

        for table in CORE_TABLES:
            assert f"create table if not exists {table}" in executed, (
                f"{table} is missing from store.schema.create_tables"
            )

    def test_create_tables_issues_the_index_ddl(self, monkeypatch):
        executed = _run(monkeypatch, schema.create_tables)

        assert "create index if not exists" in executed

    def test_ddl_is_idempotent_by_construction(self, monkeypatch):
        executed = _run(monkeypatch, schema.create_tables)

        for statement in executed.split("create table")[1:]:
            assert statement.lstrip().startswith("if not exists"), (
                "every table statement stays idempotent"
            )


class TestDatabaseDelegates:
    def test_database_still_exposes_create_tables(self, monkeypatch):
        executed = _run(monkeypatch, database._create_tables)

        assert "create table if not exists buildings" in executed

    def test_database_no_longer_carries_inline_ddl(self):
        with open(os.path.join(PROJECT_ROOT, "database.py")) as handle:
            content = handle.read()

        assert "CREATE TABLE" not in content
        assert "ALTER TABLE" not in content

    def test_database_module_shrinks_below_the_seam_budget(self):
        with open(os.path.join(PROJECT_ROOT, "database.py")) as handle:
            lines = len(handle.readlines())

        assert lines < 1300, (
            f"database.py is {lines} lines; the seam plus repositories budget is 1300"
        )
