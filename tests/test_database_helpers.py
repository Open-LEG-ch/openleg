# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for organic-growth DB helper functions."""

from contextlib import contextmanager

import database


class _FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, _query, _params=None):
        return None

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeConnection:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self):
        return _FakeCursor(self.rows)


def test_get_all_municipality_profile_bfs_numbers_returns_rows(monkeypatch):
    @contextmanager
    def _fake_get_connection():
        yield _FakeConnection([{"bfs_number": 261}, {"bfs_number": 247}])

    monkeypatch.setattr(database, "get_connection", _fake_get_connection)
    result = database.get_all_municipality_profile_bfs_numbers()
    assert sorted(result) == [247, 261]


def test_get_all_municipality_profile_bfs_numbers_returns_empty_on_error(monkeypatch):
    @contextmanager
    def _broken_get_connection():
        raise RuntimeError("db down")
        yield

    monkeypatch.setattr(database, "get_connection", _broken_get_connection)
    assert database.get_all_municipality_profile_bfs_numbers() == []


def test_get_profile_bfs_missing_elcom_tariffs_returns_rows(monkeypatch):
    @contextmanager
    def _fake_get_connection():
        yield _FakeConnection([{"bfs_number": 1001}, {"bfs_number": 1002}])

    monkeypatch.setattr(database, "get_connection", _fake_get_connection)
    assert database.get_profile_bfs_missing_elcom_tariffs(year=2026, limit=2) == [
        1001,
        1002,
    ]


def test_get_profile_bfs_missing_elcom_tariffs_returns_empty_on_error(monkeypatch):
    @contextmanager
    def _broken_get_connection():
        raise RuntimeError("db down")
        yield

    monkeypatch.setattr(database, "get_connection", _broken_get_connection)
    assert database.get_profile_bfs_missing_elcom_tariffs(year=2026, limit=2) == []
