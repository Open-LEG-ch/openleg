# SPDX-License-Identifier: AGPL-3.0-or-later

from datetime import datetime, timezone

import pytest

import database
import invoice_queries
from store import invoice_query


def test_open_question_validation_is_bounded():
    assert invoice_queries.validate_open("amount", "  Warum? ") == (
        "amount",
        "Warum?",
    )
    with pytest.raises(ValueError):
        invoice_queries.validate_open("unknown", "Warum?")
    with pytest.raises(ValueError):
        invoice_queries.validate_open("amount", "")


def test_status_history_only_moves_forward():
    invoice_queries.require_transition("open", "acknowledged")
    invoice_queries.require_transition("acknowledged", "resolved")
    with pytest.raises(ValueError):
        invoice_queries.require_transition("resolved", "open")


def test_question_deadlines_follow_operator_configuration(monkeypatch):
    monkeypatch.setenv("INVOICE_QUERY_RESPONSE_DAYS", "8")
    monkeypatch.setenv("INVOICE_QUERY_REMINDER_DAYS", "3")
    opened_at = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)

    response_due_at, reminder_due_at = invoice_queries.deadlines(opened_at)

    assert response_due_at.isoformat() == "2026-09-23T12:00:00+00:00"
    assert reminder_due_at.isoformat() == "2026-09-20T12:00:00+00:00"


def test_question_deadline_configuration_fails_closed(monkeypatch):
    monkeypatch.setenv("INVOICE_QUERY_RESPONSE_DAYS", "0")

    with pytest.raises(ValueError):
        invoice_queries.deadlines(datetime(2026, 9, 15, tzinfo=timezone.utc))

def test_operator_update_validates_transition_before_appending_message(monkeypatch):
    class Cursor:
        def __init__(self):
            self.executed = []

        def execute(self, query, params=None):
            self.executed.append((query, params))

        def fetchone(self):
            return {"status": "resolved"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Connection:
        def __init__(self, cursor):
            self._cursor = cursor

        def cursor(self):
            return self._cursor

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    cursor = Cursor()
    monkeypatch.setattr(database, "get_connection", lambda: Connection(cursor))

    with pytest.raises(ValueError):
        invoice_query.update_invoice_query(
            7, "c1", "operator", message="Antwort", target_status="open"
        )

    assert len(cursor.executed) == 1
    assert "FOR UPDATE" in cursor.executed[0][0]


class RecordingCursor:
    def __init__(self, rows=None):
        self.executed = []
        self._rows = list(rows or [])

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class SingleUseConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_due_reminders_scan_filters_by_status_deadline_and_unsent(monkeypatch):
    cursor = RecordingCursor(rows=[{"id": 3}, {"id": 5}])
    monkeypatch.setattr(database, "get_connection", lambda: SingleUseConnection(cursor))
    now = datetime(2026, 9, 16, 6, tzinfo=timezone.utc)

    due = invoice_query.due_invoice_query_reminders(now)

    assert [case["id"] for case in due] == [3, 5]
    query, params = cursor.executed[0]
    assert "status IN ('open', 'acknowledged')" in query
    assert "reminder_due_at <= %s" in query
    assert "reminder_sent_at IS NULL" in query
    assert params == (now,)


def test_mark_reminded_appends_one_event_then_stays_silent(monkeypatch):
    cursor = RecordingCursor(rows=[{"status": "open"}])
    monkeypatch.setattr(database, "get_connection", lambda: SingleUseConnection(cursor))

    assert invoice_query.mark_invoice_query_reminded(7, "system") is True

    updates = [q for q, _p in cursor.executed if "UPDATE invoice_queries" in q]
    events = [q for q, _p in cursor.executed if "INSERT INTO invoice_query_events" in q]
    assert len(updates) == 1
    assert "reminder_sent_at IS NULL" in updates[0]
    assert len(events) == 1
    assert cursor.executed[-1][1] == (7, "system", "open", "open")

    repeat = RecordingCursor()
    monkeypatch.setattr(database, "get_connection", lambda: SingleUseConnection(repeat))

    assert invoice_query.mark_invoice_query_reminded(7, "system") is False

    assert len(repeat.executed) == 1
    assert "UPDATE invoice_queries" in repeat.executed[0][0]
    assert "INSERT" not in repeat.executed[0][0]
