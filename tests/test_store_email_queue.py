# SPDX-License-Identifier: AGPL-3.0-or-later
"""Interface tests for the outbound email queue repository (store.email_queue).

Verifies the extracted module resolves the connection seam via
`database.get_connection` and that `database` re-exports the identical objects,
so legacy callers and existing monkeypatches keep working unchanged. Mirrors
`test_store_ranking.py` / `test_store_profile.py`; the seam is the test surface.
"""

import subprocess
import sys
from contextlib import contextmanager

import database
from store import email_queue

_REEXPORTED = (
    "schedule_email",
    "get_pending_emails",
    "mark_email_sent",
    "mark_email_failed",
    "cancel_emails_for_building",
    "get_email_stats",
)


class _FakeCursor:
    def __init__(self, rows=None, one=None, rowcount=0):
        self.rows = rows or []
        self.one = one
        self.rowcount = rowcount
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


def _conn_ctx(cursor):
    @contextmanager
    def _factory():
        yield _FakeConnection(cursor)

    return _factory


def test_database_reexports_are_identical_objects():
    for name in _REEXPORTED:
        assert getattr(database, name) is getattr(email_queue, name), name


def test_store_email_queue_imports_without_database_bootstrap():
    result = subprocess.run(
        [sys.executable, "-c", "import store.email_queue; print('ok')"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_email_queue_uses_database_connection_seam(monkeypatch):
    # Monkeypatching database.get_connection must affect store.email_queue calls,
    # proving the seam is shared (not a stale direct import binding).
    cur = _FakeCursor(rows=[{"id": 1, "email": "a@b.ch"}])
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    rows = email_queue.get_pending_emails()
    assert rows == [{"id": 1, "email": "a@b.ch"}]
    assert "scheduled_emails" in cur.executed[0][0]
    assert cur.executed[0][1] == (50,)


def test_schedule_email_skips_duplicate(monkeypatch):
    # An already pending/sent template for the building returns False without insert.
    cur = _FakeCursor(one=(1,))
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert email_queue.schedule_email("b1", "a@b.ch", "welcome", 0.0) is False
