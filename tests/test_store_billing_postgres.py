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
        monkeypatch.setattr(db, "_pool_pid", os.getpid())
        db.create_tables()
        yield db
    finally:
        if pool:
            pool.closeall()
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.close()


def _insert_issued_invoice(invoice_number, *, participant_id=MEMBER):
    """Insert a synthetic issued invoice bound to its participant registration."""
    if not db.get_building(participant_id):
        assert db.save_building(
            participant_id,
            participant_id + "@example.invalid",
            {"address": "Synthetic invoice owner", "lat": 47.2, "lon": 8.2},
            {},
            verified=True,
        )
    registration = db.get_building(participant_id)["referral_code"]
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
                json.dumps(
                    {"issuer": "LEG Test", "participant_registration_id": registration}
                ),
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


@pytest.mark.parametrize("reconciliation", [None, "malformed"])
def test_save_rejects_malformed_registration_evidence(billing_database, reconciliation):
    """Bad save inputs fail through the registration-domain contract."""
    from store.invoice_identity import InvoiceRegistrationMismatch

    with pytest.raises(InvoiceRegistrationMismatch):
        billing.save_billing_period(
            COMMUNITY, "2026-09-01", "2026-10-01", {"reconciliation": reconciliation}
        )


@pytest.mark.parametrize("missing_code", [None, ""])
def test_schema_repairs_missing_registration_tokens_without_rebinding_invoices(
    billing_database, missing_code
):
    """Legacy profiles gain stable future identities without inheriting old invoices."""
    invoice = _insert_issued_invoice("LEGACY-2026-000001")
    with db.get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            "UPDATE buildings SET referral_code=%s WHERE building_id=%s",
            (missing_code, MEMBER),
        )
        cursor.execute(
            "SELECT row_to_json(i) AS snapshot FROM invoices i WHERE id=%s", (invoice,)
        )
        frozen = cursor.fetchone()["snapshot"]
    db.create_tables()
    token = db.get_building(MEMBER)["referral_code"]
    assert isinstance(token, str) and token
    assert billing.get_invoices_for_participant(MEMBER) == []
    db.create_tables()
    assert db.get_building(MEMBER)["referral_code"] == token
    with db.get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT row_to_json(i) AS snapshot FROM invoices i WHERE id=%s", (invoice,)
        )
        assert cursor.fetchone()["snapshot"] == frozen


@pytest.mark.parametrize("status", ["draft", "issued"])
def test_schema_preserves_invoice_bearing_periods_without_registration_proofs(
    billing_database, status
):
    """Recovery never invalidates a period that already has an invoice."""
    with db.get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO billing_periods(community_id,period_start,period_end,status) "
            "VALUES (%s,'2026-09-01','2026-10-01',%s) RETURNING id",
            (COMMUNITY, status),
        )
        period = cursor.fetchone()["id"]
        cursor.execute(
            "INSERT INTO invoices(billing_period_id,community_id,status) VALUES (%s,%s,'issued')",
            (period, COMMUNITY),
        )
        cursor.execute(
            "SELECT row_to_json(p) AS snapshot FROM billing_periods p WHERE id=%s",
            (period,),
        )
        frozen = cursor.fetchone()["snapshot"]
    db.create_tables()
    with db.get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT row_to_json(p) AS snapshot FROM billing_periods p WHERE id=%s",
            (period,),
        )
        assert cursor.fetchone()["snapshot"] == frozen
