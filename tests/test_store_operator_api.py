# SPDX-License-Identifier: AGPL-3.0-or-later
"""Transactional claim contract for operator webhook workers."""

from contextlib import contextmanager

import pytest

import database
from store import operator_api as store


class Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


@contextmanager
def _connection(cursor):
    class Connection:
        def cursor(self):
            return cursor

    yield Connection()


def test_delivery_batch_is_bounded_and_claimed_before_worker_receives_it(monkeypatch):
    cursor = Cursor([{"delivery_id": "delivery-1", "status": "processing"}])
    monkeypatch.setattr(database, "get_connection", lambda: _connection(cursor))

    rows = store.get_pending_deliveries(max_attempts=5, limit=1000)

    assert rows == [{"delivery_id": "delivery-1", "status": "processing"}]
    sql, params = cursor.executed[0]
    assert "FOR UPDATE OF d SKIP LOCKED" in sql
    assert "SET status='processing'" in sql
    assert "claim_id=gen_random_uuid()" in sql
    assert "e.schema_version = 'operator-event/1'" in sql
    assert params == (5, 100)


def test_second_worker_cannot_select_a_fresh_processing_claim(monkeypatch):
    first = Cursor([{"delivery_id": "delivery-1", "status": "processing"}])
    second = Cursor([])
    cursors = iter((first, second))
    monkeypatch.setattr(database, "get_connection", lambda: _connection(next(cursors)))

    assert store.get_pending_deliveries() == [
        {"delivery_id": "delivery-1", "status": "processing"}
    ]
    assert store.get_pending_deliveries() == []
    assert "d.status='processing' AND d.claimed_at <" in second.executed[0][0]


def test_delivery_completion_rejects_non_terminal_claim_state():
    with pytest.raises(ValueError, match="Invalid completed"):
        store.record_attempt(
            "delivery-1",
            claim_id="claim-1",
            status="processing",
            response_status=200,
            retryable=False,
        )


def test_only_the_current_worker_claim_can_complete_delivery(monkeypatch):
    cursor = Cursor([])
    monkeypatch.setattr(database, "get_connection", lambda: _connection(cursor))

    store.record_attempt(
        "delivery-1",
        claim_id="claim-current",
        status="delivered",
        response_status=204,
        retryable=False,
    )

    sql, params = cursor.executed[0]
    assert "status='processing' AND claim_id=%s" in sql
    assert params[-2:] == ("delivery-1", "claim-current")


def test_manual_retry_resets_attempt_count(monkeypatch):
    cursor = Cursor(
        [{"delivery_id": "delivery-1", "status": "retry", "attempt_count": 0}]
    )
    monkeypatch.setattr(database, "get_connection", lambda: _connection(cursor))

    result = store.retry_delivery("community-1", "delivery-1")

    assert result["attempt_count"] == 0
    sql, _params = cursor.executed[0]
    assert "attempt_count=0" in sql


def test_credential_rotation_changes_the_webhook_secret_version(monkeypatch):
    cursor = Cursor([{"id": "client-1", "webhook_secret_version": 2}])
    monkeypatch.setattr(database, "get_connection", lambda: _connection(cursor))

    result = store.rotate_client("community-1", "client-1", "hash")

    assert result["webhook_secret_version"] == 2
    sql, _params = cursor.executed[0]
    assert "webhook_secret_version=webhook_secret_version+1" in sql
