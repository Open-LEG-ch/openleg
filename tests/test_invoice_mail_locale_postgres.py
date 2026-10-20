# SPDX-License-Identifier: AGPL-3.0-or-later
"""Issued invoice notices follow the invoiced member's stored language."""

import json
from datetime import date

import pytest

import dashboard
from tests import test_interest_postgres

interest_database = test_interest_postgres.interest_database
pytestmark = pytest.mark.integration


@pytest.mark.parametrize("locale", ["fr_CH", "de_CH"])
def test_invoice_notice_uses_member_language_and_keeps_delivery_idempotent(
    interest_database, locale
):
    db = interest_database
    for identifier, preferred in (("operator", "de_CH"), ("member", locale)):
        assert db.save_building(
            identifier,
            identifier + "@example.com",
            {"address": "Synthetic private address", "lat": 47.2, "lon": 8.2},
            {},
            verified=True,
            preferred_locale=preferred,
        )
    community = db.create_community_record("Synthetic LEG", "operator", "simple", "")
    policy = {"delivery_method": "email"}
    with db.get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO invoices (
                community_id, participant_id, invoice_number, status,
                policy_snapshot, provenance_snapshot, line_items_snapshot,
                net_chf, vat_rate_pct, vat_chf, gross_chf, issue_date, due_date
            ) VALUES (%s, 'member', 'SYNTHETIC-INVOICE-1', 'issued', %s, '{}',
                      '[]', 9.67, 0, 0, 9.67, %s, %s)
            RETURNING id
            """,
            (community, json.dumps(policy), date(2026, 10, 19), date(2026, 11, 18)),
        )
        invoice = cursor.fetchone()["id"]
    captured = []

    def send(*args, **kwargs):
        captured.append(args)
        return True

    result = dashboard.leg_deliver_invoice(
        community,
        "operator",
        invoice,
        send_email=send,
        invoice_url=f"https://example.invalid/dashboard/invoices/{invoice}",
    )
    assert result["error"] is None
    assert len(captured) == 1
    recipient, subject, body = captured[0]
    assert recipient == "member@example.com"
    assert ("Votre facture" if locale == "fr_CH" else "Ihre Rechnung") in subject
    assert "SYNTHETIC-INVOICE-1" in subject
    assert "lang=" + locale.replace("_", "-") in body
    assert "Synthetic private address" not in body
    assert "9.67" not in body
    repeated = dashboard.leg_deliver_invoice(
        community,
        "operator",
        invoice,
        send_email=send,
        invoice_url=f"https://example.invalid/dashboard/invoices/{invoice}",
    )
    assert repeated["error"] is None
    assert len(captured) == 1
    with db.get_connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT * FROM invoices WHERE id = %s", (invoice,))
        stored = cursor.fetchone()
        assert stored["policy_snapshot"] == policy
        assert str(stored["gross_chf"]) == "9.67"
        cursor.execute(
            "SELECT COUNT(*) AS count FROM invoice_delivery_jobs WHERE invoice_id = %s",
            (invoice,),
        )
        assert cursor.fetchone()["count"] == 1
