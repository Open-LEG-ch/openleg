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
    assert "e.schema_version='operator-event/1'" in sql
    assert params == (5, 100)


def test_fresh_processing_claim_is_not_selected_by_a_second_worker(monkeypatch):
    cursor = Cursor([])
    monkeypatch.setattr(database, "get_connection", lambda: _connection(cursor))

    assert store.get_pending_deliveries() == []
    assert "d.status='processing' AND d.claimed_at <" in cursor.executed[0][0]


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


def test_reenqueued_transition_suppresses_duplicate_deliveries(monkeypatch):
    cursor = Cursor([])
    monkeypatch.setattr(database, "get_connection", lambda: _connection(cursor))

    event_id = store.enqueue_event(
        cursor, "formation.submitted", "case-1", "community-a", {"status": "prepared"}
    )
    replayed_id = store.enqueue_event(
        Cursor([]),
        "formation.submitted",
        "case-1",
        "community-a",
        {"status": "prepared"},
    )

    assert (
        replayed_id == event_id == store.event_id_for("formation.submitted", "case-1")
    )
    event_sql, event_params = cursor.executed[0]
    assert "ON CONFLICT (event_id) DO NOTHING" in event_sql
    assert "'operator-event/1'" in event_sql
    assert event_params[:4] == (
        event_id,
        "formation.submitted",
        "case-1",
        "community-a",
    )
    delivery_sql, delivery_params = cursor.executed[1]
    assert "ON CONFLICT (event_id,client_id) DO NOTHING" in delivery_sql
    assert "active=TRUE AND webhook_url IS NOT NULL" in delivery_sql
    assert delivery_params == (
        event_id,
        "community-a",
        "formation.read",
        "formation.read",
    )
