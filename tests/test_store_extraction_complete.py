# SPDX-License-Identifier: AGPL-3.0-or-later
"""The last domains leave database.py.

docs/architecture.md states the end state: extraction is finished when nothing
but the pool, the schema, and the re-exports remain. These tests pin that end
state and the four new repositories that get it there.
"""

import ast
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

import database
from store import analytics, consent, document, municipality, ops

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DOMAINS = {
    analytics: ("track_event", "get_stats"),
    consent: ("save_data_consent", "get_data_consent", "count_consented_buildings"),
    document: (
        "update_document_signing_status",
        "store_leg_document",
        "get_leg_document",
        "list_leg_documents",
    ),
    ops: (
        "save_lea_report",
        "get_lea_reports",
        "save_ops_snapshot",
        "get_ops_snapshots",
    ),
    municipality: (
        "save_municipality",
        "get_all_municipalities",
        "update_municipality_status",
    ),
}


class _FakeCursor:
    def __init__(self, rows=None, one=None, rowcount=1, description=None):
        self.rows = rows or []
        self.one = one
        self.rowcount = rowcount
        self.description = description
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.one

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def commit(self):
        return None


def _conn_ctx(cursor):
    @contextmanager
    def _factory():
        yield _FakeConnection(cursor)

    return _factory


@pytest.mark.parametrize(
    "module, names",
    [pytest.param(m, n, id=m.__name__.rsplit(".", 1)[-1]) for m, n in DOMAINS.items()],
)
def test_database_reexports_are_identical_objects(module, names):
    for name in names:
        assert getattr(database, name) is getattr(module, name), name


@pytest.mark.parametrize(
    "module_name", ("analytics", "consent", "document", "ops", "municipality")
)
def test_each_store_imports_without_database_bootstrap(module_name):
    result = subprocess.run(
        [sys.executable, "-c", f"import store.{module_name}; print('ok')"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_track_event_uses_the_database_connection_seam(monkeypatch):
    cur = _FakeCursor()
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    analytics.track_event("registration", "building-1", {"type": "owner"})

    query, _params = cur.executed[0]
    assert "analytics_events" in query


def test_count_consented_buildings_reads_through_the_seam(monkeypatch):
    cur = _FakeCursor(one={"count": 3})
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert consent.count_consented_buildings() == 3


def test_list_leg_documents_returns_the_rows_as_dicts(monkeypatch):
    """The pool uses RealDictCursor, so a row is already a mapping.

    This one zipped cur.description against the row instead, and iterating a
    dict yields its keys, so every document came back as {column: column}. The
    same RealDictCursor mistake was fixed in store_leg_document before; this
    function was missed.
    """
    rows = [
        {"id": 1, "doc_type": "gemeinschaftsvereinbarung", "filename": "gv.pdf"},
        {"id": 2, "doc_type": "teilnehmervertrag", "filename": "tv.pdf"},
    ]
    monkeypatch.setattr(database, "get_connection", _conn_ctx(_FakeCursor(rows=rows)))

    assert document.list_leg_documents("community-a") == rows


def test_get_lea_reports_reads_through_the_seam(monkeypatch):
    rows = [{"job_name": "nightly", "status": "ok"}]
    monkeypatch.setattr(database, "get_connection", _conn_ctx(_FakeCursor(rows=rows)))

    assert ops.get_lea_reports() == rows


@pytest.mark.parametrize(
    "call",
    (
        pytest.param(lambda: analytics.get_stats(), id="get_stats"),
        pytest.param(lambda: consent.get_data_consent("b1"), id="get_data_consent"),
        pytest.param(lambda: document.get_leg_document(1), id="get_leg_document"),
        pytest.param(lambda: ops.get_lea_reports(), id="get_lea_reports"),
        pytest.param(
            lambda: municipality.get_all_municipalities(), id="get_all_municipalities"
        ),
    ),
)
def test_a_broken_connection_never_leaks_the_exception(monkeypatch, call):
    @contextmanager
    def _broken():
        raise RuntimeError("db down")
        yield

    monkeypatch.setattr(database, "get_connection", _broken)

    assert call() in ({}, [], None, 0)


# ---------------------------------------------------------------------------
# The end state
# ---------------------------------------------------------------------------


def test_database_keeps_only_the_pool_the_schema_and_the_reexports():
    tree = ast.parse(
        (PROJECT_ROOT / "database.py").read_text(encoding="utf-8"),
        filename="database.py",
    )
    defined = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}

    assert defined == {"init_db", "get_connection", "_create_tables", "is_db_available"}


def test_the_dead_json_migration_is_gone():
    """A pre-PostgreSQL import path with no caller anywhere in the repository."""
    assert not hasattr(database, "migrate_from_json")


def test_the_extraction_order_is_empty():
    text = (PROJECT_ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")

    for store_name in (
        "store/analytics",
        "store/consent",
        "store/document",
        "store/ops",
    ):
        assert store_name in text
    assert "Remaining in `database.py`" not in text
