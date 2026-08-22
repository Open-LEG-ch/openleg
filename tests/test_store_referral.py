# SPDX-License-Identifier: AGPL-3.0-or-later
"""Interface tests for the referral repository (store.referral)."""

import subprocess
import sys
from contextlib import contextmanager
from typing import ClassVar

import database
from store import referral
from tests.consent_visibility import filters_by_consent

_REEXPORTED = (
    "get_referral_code",
    "get_building_by_referral_code",
    "get_referral_stats",
    "get_referral_leaderboard",
)


class _FakeCursor:
    def __init__(self, rows=None, one=None):
        self.rows = rows or []
        self.one = one
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
        assert getattr(database, name) is getattr(referral, name), name


def test_store_referral_imports_without_database_bootstrap():
    result = subprocess.run(
        [sys.executable, "-c", "import store.referral; print('ok')"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_referral_uses_database_connection_seam(monkeypatch):
    cur = _FakeCursor(one={"referral_code": "BADEN42"})
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert referral.get_referral_code("building-1") == "BADEN42"
    assert "FROM buildings" in cur.executed[0][0]
    assert cur.executed[0][1] == ("building-1",)


class _LeaderboardCursor(_FakeCursor):
    """Returns the referrers the query as written would really return."""

    ROWS = (
        {"building_id": "consented", "street": "Badstrasse 1", "referral_count": 4},
        {"building_id": "revoked", "street": "Badstrasse 3", "referral_count": 9},
        {
            "building_id": "never-consented",
            "street": "Badstrasse 5",
            "referral_count": 7,
        },
    )
    CONSENTS: ClassVar[dict] = {"consented": True, "revoked": False}

    def fetchall(self):
        query = self.executed[-1][0]
        rows = [dict(row) for row in self.ROWS]
        if filters_by_consent(query):
            rows = [
                row for row in rows if self.CONSENTS.get(row["building_id"]) is True
            ]
        return rows


def test_leaderboard_excludes_revoked_and_missing_consent(monkeypatch):
    """The top referrer is the one who revoked sharing; the board must not name them."""
    cur = _LeaderboardCursor()
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    rows = referral.get_referral_leaderboard(limit=5, city_id="baden")

    assert [row["building_id"] for row in rows] == ["consented"]


def test_leaderboard_keeps_city_scope_and_limit(monkeypatch):
    cur = _LeaderboardCursor()
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    referral.get_referral_leaderboard(limit=5, city_id="baden")

    query, params = cur.executed[0]
    assert "b.city_id = %s" in query
    assert "LIMIT %s" in query
    assert params == ("baden", 5)


def test_missing_referral_stats_default_to_zero(monkeypatch):
    cur = _FakeCursor(one=None)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert referral.get_referral_stats("missing") == {"total_referrals": 0}
