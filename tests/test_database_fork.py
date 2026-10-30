# SPDX-License-Identifier: AGPL-3.0-or-later
"""The connection pool across fork(), as gunicorn --preload does it.

With --preload the master runs create_app(), which opens the pool. Every worker
is forked from that master and inherits the same PostgreSQL sockets. Two
workers then talk over one connection, and concurrent requests read each
other's results ("PGRES_TUPLES_OK and no message from the libpq", "no results
to fetch"). A process that did not create the pool must open its own.
"""

import os
from unittest.mock import MagicMock

import pytest

import database as db


@pytest.fixture
def fresh_pool_state(monkeypatch):
    monkeypatch.setattr(db, "_connection_pool", None)
    monkeypatch.setattr(db, "_pool_pid", None)
    monkeypatch.setattr(db, "_inherited_pools", [])


def test_inherited_pool_is_replaced_and_kept_open(monkeypatch, fresh_pool_state):
    inherited = MagicMock(name="inherited")
    own = MagicMock(name="own")
    factory = MagicMock(return_value=own)
    monkeypatch.setattr(db.pool, "ThreadedConnectionPool", factory)
    monkeypatch.setattr(db, "DATABASE_URL", "postgresql://example.invalid/openleg")
    monkeypatch.setattr(db, "_connection_pool", inherited)
    monkeypatch.setattr(db, "_pool_pid", os.getpid() + 1)

    with db.get_connection() as conn:
        assert conn is own.getconn.return_value

    factory.assert_called_once()
    own.putconn.assert_called_once_with(conn)
    inherited.getconn.assert_not_called()
    # Closing the inherited pool would end the sessions the parent still owns.
    inherited.closeall.assert_not_called()
    assert db._inherited_pools == [inherited]
    assert db._pool_pid == os.getpid()


def test_pool_created_here_is_used_as_is(monkeypatch, fresh_pool_state):
    own = MagicMock(name="own")
    factory = MagicMock()
    monkeypatch.setattr(db.pool, "ThreadedConnectionPool", factory)
    monkeypatch.setattr(db, "_connection_pool", own)
    monkeypatch.setattr(db, "_pool_pid", os.getpid())

    with db.get_connection() as conn:
        assert conn is own.getconn.return_value

    factory.assert_not_called()


@pytest.mark.integration
@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs os.fork")
def test_forked_child_uses_its_own_postgres_session(monkeypatch, fresh_pool_state):
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("requires PostgreSQL DATABASE_URL")
    monkeypatch.setattr(db, "DATABASE_URL", url)
    monkeypatch.setattr(db, "_create_tables", lambda: None)
    assert db.init_db()
    parent_pool = db._connection_pool

    def backend_pid():
        with db.get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT pg_backend_pid() AS pid")
            return cur.fetchone()["pid"]

    try:
        parent_backend = backend_pid()
        read_end, write_end = os.pipe()
        child = os.fork()
        if child == 0:
            status = 1
            try:
                os.close(read_end)
                os.write(write_end, str(backend_pid()).encode())
                status = 0
            finally:
                os._exit(status)
        os.close(write_end)
        _, status = os.waitpid(child, 0)
        with os.fdopen(read_end) as pipe:
            child_backend = int(pipe.read() or 0)

        assert os.waitstatus_to_exitcode(status) == 0
        assert child_backend not in (0, parent_backend)
        # The parent's session survived the child.
        assert backend_pid() == parent_backend
    finally:
        parent_pool.closeall()
