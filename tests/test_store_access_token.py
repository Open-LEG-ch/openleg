# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contracts for the hashed, single-use access-token repository, both kinds.

The dashboard and municipality tables were served by two repository modules
with identical SQL and a different noun. One module now issues both statements
from one code path, with the table and the subject column coming from module
constants and never from a caller.
"""

from contextlib import contextmanager

import pytest

import database
from store import access_token


class _FakeCursor:
    def __init__(self, *, one=None, rowcount=0):
        self.one = one
        self.rowcount = rowcount
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

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


def _conn_ctx(cursor):
    @contextmanager
    def _factory():
        yield _FakeConnection(cursor)

    return _factory


DASHBOARD = pytest.param(
    "dashboard_access_tokens",
    "building_id",
    "building-1",
    "save_dashboard_access_token",
    "consume_dashboard_access_token",
    "revoke_dashboard_access_tokens",
    id="dashboard",
)
MUNICIPALITY = pytest.param(
    "municipality_access_tokens",
    "municipality_id",
    7,
    "save_municipality_access_token",
    "consume_municipality_access_token",
    "revoke_municipality_access_tokens",
    id="municipality",
)
KINDS = (DASHBOARD, MUNICIPALITY)
SIGNATURE = "table, column, subject, save_name, consume_name, revoke_name"


@pytest.mark.parametrize(SIGNATURE, KINDS)
def test_database_reexports_the_access_token_repository(
    table, column, subject, save_name, consume_name, revoke_name
):
    for name in (save_name, consume_name, revoke_name):
        assert getattr(database, name) is getattr(access_token, name)


@pytest.mark.parametrize(SIGNATURE, KINDS)
def test_save_persists_only_the_hash_and_the_expiry(
    monkeypatch, table, column, subject, save_name, consume_name, revoke_name
):
    cursor = _FakeCursor(rowcount=1)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cursor))

    assert getattr(access_token, save_name)("a" * 64, subject, ttl_seconds=1800)

    query, params = cursor.executed[0]
    normalized = " ".join(query.split())
    assert f"INSERT INTO {table}" in normalized
    assert "token_hash" in normalized
    assert column in normalized
    assert "%s * INTERVAL '1 second'" in normalized
    assert params == ("a" * 64, subject, 1800)


@pytest.mark.parametrize(SIGNATURE, KINDS)
def test_save_fails_closed_on_an_impossible_hash_collision(
    monkeypatch, table, column, subject, save_name, consume_name, revoke_name
):
    cursor = _FakeCursor(rowcount=0)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cursor))

    assert not getattr(access_token, save_name)("a" * 64, subject, ttl_seconds=1800)

    query, _params = cursor.executed[0]
    normalized = " ".join(query.split())
    assert "ON CONFLICT (token_hash) DO NOTHING" in normalized
    assert "DO UPDATE" not in normalized


@pytest.mark.parametrize(SIGNATURE, KINDS)
def test_consume_is_atomic_and_rejects_expired_used_or_revoked_tokens(
    monkeypatch, table, column, subject, save_name, consume_name, revoke_name
):
    cursor = _FakeCursor(one={column: subject})
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cursor))

    assert getattr(access_token, consume_name)("a" * 64) == {column: subject}

    query, params = cursor.executed[0]
    normalized = " ".join(query.split())
    assert normalized.startswith(f"UPDATE {table}")
    assert "SET used_at = CURRENT_TIMESTAMP" in normalized
    assert "expires_at > CURRENT_TIMESTAMP" in normalized
    assert "used_at IS NULL" in normalized
    assert "revoked_at IS NULL" in normalized
    assert f"RETURNING {column}" in normalized
    assert params == ("a" * 64,)


@pytest.mark.parametrize(SIGNATURE, KINDS)
def test_consume_returns_none_when_no_row_survives_the_guards(
    monkeypatch, table, column, subject, save_name, consume_name, revoke_name
):
    cursor = _FakeCursor(one=None)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cursor))

    assert getattr(access_token, consume_name)("a" * 64) is None


@pytest.mark.parametrize(SIGNATURE, KINDS)
def test_revoke_invalidates_every_unused_token_for_the_subject(
    monkeypatch, table, column, subject, save_name, consume_name, revoke_name
):
    cursor = _FakeCursor(rowcount=2)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cursor))

    assert getattr(access_token, revoke_name)(subject) == 2

    query, params = cursor.executed[0]
    normalized = " ".join(query.split())
    assert normalized.startswith(f"UPDATE {table}")
    assert "SET revoked_at = CURRENT_TIMESTAMP" in normalized
    assert "used_at IS NULL" in normalized
    assert params == (subject,)


@pytest.mark.parametrize(SIGNATURE, KINDS)
def test_a_broken_connection_never_leaks_the_exception(
    monkeypatch, table, column, subject, save_name, consume_name, revoke_name
):
    @contextmanager
    def _broken():
        raise RuntimeError("db down")
        yield

    monkeypatch.setattr(database, "get_connection", _broken)

    assert getattr(access_token, save_name)("a" * 64, subject, ttl_seconds=900) is False
    assert getattr(access_token, consume_name)("a" * 64) is None
    assert getattr(access_token, revoke_name)(subject) == 0


@pytest.mark.parametrize(
    "module", ("store.dashboard_access", "store.municipality_access")
)
def test_the_duplicated_store_modules_are_gone(module):
    with pytest.raises(ModuleNotFoundError):
        __import__(module)


def test_the_table_names_come_from_constants_not_from_callers():
    """A caller must never be able to steer the statement at a different table."""
    import inspect

    for name in ("save_dashboard_access_token", "save_municipality_access_token"):
        signature = inspect.signature(getattr(access_token, name))
        assert "table" not in signature.parameters
        assert "column" not in signature.parameters
