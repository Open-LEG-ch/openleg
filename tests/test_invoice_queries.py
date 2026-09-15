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
