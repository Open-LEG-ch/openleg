# SPDX-License-Identifier: AGPL-3.0-or-later
"""Member invoice reads through the real PostgreSQL schema.

The fake-cursor tests in test_store_billing.py pin the query shape, but only
PostgreSQL resolves column references. The member invoice list joins
``invoices`` twice (original and replacement), so every unqualified invoice
column is ambiguous there and fails at parse time, even on an empty table.
"""

import json
import os
import secrets
from datetime import date
from decimal import Decimal
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import psycopg2.extras
import psycopg2.pool
import pytest

import database as db
from store import billing

pytestmark = pytest.mark.integration

COMMUNITY = "community-pg"
MEMBER = "member-building"


@pytest.fixture
def billing_database(monkeypatch):
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("requires PostgreSQL DATABASE_URL")
    parsed = urlsplit(url)
    name = f"openleg_billing_{secrets.token_hex(6)}"
    admin = psycopg2.connect(urlunsplit(parsed._replace(path="/postgres")))
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    pool = None
    try:
        pool = psycopg2.pool.ThreadedConnectionPool(
            1,
            2,
            urlunsplit(parsed._replace(path=f"/{name}")),
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
        monkeypatch.setattr(db, "_connection_pool", pool)
        db.create_tables()
        yield db
    finally:
        if pool:
            pool.closeall()
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.close()


def _insert_issued_invoice(invoice_number, *, participant_id=MEMBER):
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO invoices (
                community_id, participant_id, invoice_number, status,
                policy_snapshot, provenance_snapshot, line_items_snapshot,
                net_chf, vat_rate_pct, vat_chf, gross_chf, issue_date, due_date
            )
            VALUES (%s, %s, %s, 'issued', %s, %s, %s, 9.67, 0, 0, 9.67, %s, %s)
            RETURNING id
            """,
            (
                COMMUNITY,
                participant_id,
                invoice_number,
                json.dumps({"internal_price_rp_kwh": "11.00"}),
                json.dumps({"issuer": "LEG Test"}),
                json.dumps([{"kind": "consumer_charge", "amount_chf": "9.67"}]),
                date(2026, 10, 17),
                date(2026, 11, 16),
            ),
        )
        return cur.fetchone()["id"]


def test_member_invoice_list_resolves_on_the_real_schema(billing_database):
    assert billing.get_invoices_for_participant(MEMBER) == []


def test_member_invoice_list_returns_own_issued_invoice(billing_database):
    own_id = _insert_issued_invoice("LEGT-2026-000001")
    _insert_issued_invoice("LEGT-2026-000002", participant_id="someone-else")

    rows = billing.get_invoices_for_participant(MEMBER)

    assert [row["id"] for row in rows] == [own_id]
    row = rows[0]
    assert row["invoice_number"] == "LEGT-2026-000001"
    assert row["policy_snapshot"] == {"internal_price_rp_kwh": "11.00"}
    assert row["gross_chf"] == Decimal("9.67")
    assert row["issue_date"] == date(2026, 10, 17)
    assert row["lifecycle_state"] == "issued"
    assert row["corrects_invoice_number"] is None
    assert row["corrected_by_invoice_number"] is None
