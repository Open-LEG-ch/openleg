# SPDX-License-Identifier: AGPL-3.0-or-later

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
