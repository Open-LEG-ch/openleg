# SPDX-License-Identifier: AGPL-3.0-or-later

import pytest

import invoice_queries


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
