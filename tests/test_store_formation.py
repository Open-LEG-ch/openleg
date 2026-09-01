# SPDX-License-Identifier: AGPL-3.0-or-later
"""Interface tests for the formation repository module (store.formation).

The formation repository owns the SQL for LEG formation: communities,
community members, and the consent-gated neighbour search.
"""

import subprocess
import sys
from contextlib import contextmanager
from unittest.mock import MagicMock

import database
from store import formation

REEXPORTED = (
    "confirm_invited_member",
    "count_confirmed_members",
    "create_community_record",
    "fetch_community_with_members",
    "fetch_nearby_consenting_neighbours",
    "fetch_user_communities",
    "insert_invited_member",
    "mark_formation_started",
    "submit_community_to_dso",
)


class _FakeCursor:
    def __init__(self, one=None, rows=None, rowcount=1):
        self.rowcount = rowcount
        self.one = one
        self.rows = rows or []
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.rows

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


def _events(monkeypatch):
    events = MagicMock()
    monkeypatch.setattr(database, "track_event", events)
    return events


def test_database_reexports_are_identical_objects():
    for name in REEXPORTED:
        assert getattr(database, name) is getattr(formation, name), name


def test_store_formation_imports_without_database_bootstrap():
    result = subprocess.run(
        [sys.executable, "-c", "import store.formation; print('ok')"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_create_community_record_inserts_community_and_admin(monkeypatch):
    cur = _FakeCursor()
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    community_id = formation.create_community_record(
        "LEG Musterweg", "b-admin", "simple", ""
    )

    assert community_id
    assert len(cur.executed) == 2
    community_sql, community_params = cur.executed[0]
    assert "INSERT INTO communities" in community_sql
    assert "interested" in community_params
    member_sql, member_params = cur.executed[1]
    assert "INSERT INTO community_members" in member_sql
    assert member_params[:2] == (community_id, "b-admin")


def test_insert_invited_member_blocks_duplicates(monkeypatch):
    cur = _FakeCursor(one={"1": 1})
    events = _events(monkeypatch)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert formation.insert_invited_member("c1", "b2", "b1") is False
    assert len(cur.executed) == 1
    events.assert_not_called()


def test_insert_invited_member_tracks_the_invitation(monkeypatch):
    cur = _FakeCursor(one=None)
    events = _events(monkeypatch)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert formation.insert_invited_member("c1", "b2", "b1") is True
    insert_sql, insert_params = cur.executed[1]
    assert "INSERT INTO community_members" in insert_sql
    assert insert_params[2:4] == ("member", "invited")
    events.assert_called_once_with(
        "member_invited", "b2", {"community_id": "c1", "invited_by": "b1"}
    )


def test_confirm_invited_member_requires_an_open_invitation(monkeypatch):
    cur = _FakeCursor(rowcount=0)
    events = _events(monkeypatch)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert formation.confirm_invited_member("c1", "b2") is False
    events.assert_not_called()


def test_confirm_invited_member_tracks_the_confirmation(monkeypatch):
    cur = _FakeCursor(rowcount=1)
    events = _events(monkeypatch)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert formation.confirm_invited_member("c1", "b2") is True
    update_sql = cur.executed[0][0]
    assert "UPDATE community_members" in update_sql
    assert "invited" in update_sql
    events.assert_called_once_with("member_confirmed", "b2", {"community_id": "c1"})


def test_count_confirmed_members_reads_the_confirmed_count(monkeypatch):
    cur = _FakeCursor(one={"count": 4})
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert formation.count_confirmed_members("c1") == 4
    query, params = cur.executed[0]
    assert "community_members" in query
    assert "confirmed" in query
    assert params == ("c1",)


def test_mark_formation_started_updates_the_community(monkeypatch):
    cur = _FakeCursor()
    events = _events(monkeypatch)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert formation.mark_formation_started("c1") is True
    query, params = cur.executed[0]
    assert "UPDATE communities" in query
    assert "formation_started" in params
    events.assert_called_once_with("formation_started", None, {"community_id": "c1"})


def test_submit_community_to_dso_requires_signatures_pending(monkeypatch):
    cur = _FakeCursor(rowcount=0)
    events = _events(monkeypatch)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert formation.submit_community_to_dso("c1") is False
    events.assert_not_called()


def test_submit_community_to_dso_tracks_the_submission(monkeypatch):
    cur = _FakeCursor(rowcount=1)
    events = _events(monkeypatch)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert formation.submit_community_to_dso("c1") is True
    query, params = cur.executed[0]
    assert "UPDATE communities" in query
    assert "signatures_pending" in params
    events.assert_called_once_with("dso_submitted", None, {"community_id": "c1"})


def test_fetch_community_with_members_reads_the_aggregate(monkeypatch):
    row = {"community_id": "c1", "members": []}
    cur = _FakeCursor(one=row)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert formation.fetch_community_with_members("c1") is row
    query, params = cur.executed[0]
    assert "array_agg" in query
    assert params == ("c1",)


def test_fetch_user_communities_reads_the_membership_rows(monkeypatch):
    rows = [{"community_id": "c1", "role": "admin"}]
    cur = _FakeCursor(rows=rows)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert formation.fetch_user_communities("b1") == rows
    query, params = cur.executed[0]
    assert "community_members" in query
    assert params == ("b1",)


def test_a_broken_connection_never_leaks_the_exception(monkeypatch):
    @contextmanager
    def _broken():
        raise RuntimeError("db down")
        yield

    monkeypatch.setattr(database, "get_connection", _broken)
    _events(monkeypatch)

    assert formation.create_community_record("n", "b", "simple", "") is None
    assert formation.insert_invited_member("c1", "b2", "b1") is False
    assert formation.confirm_invited_member("c1", "b2") is False
    assert formation.count_confirmed_members("c1") is None
    assert formation.mark_formation_started("c1") is False
    assert formation.submit_community_to_dso("c1") is False
    assert formation.fetch_community_with_members("c1") is None
    assert formation.fetch_user_communities("b1") is None
    assert formation.fetch_nearby_consenting_neighbours("b1", 150) is None
