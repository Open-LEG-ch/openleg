# SPDX-License-Identifier: AGPL-3.0-or-later
"""Retained invoices belong to the original household registration."""

import pytest

from tests import test_sandbox_http
from tests.test_sandbox_billing import COMMUNITY, _import, _policy, _prepare
from tests.test_sandbox_metering import _workspace_connection
from tests.test_sandbox_onboarding import _mail_link
from tests.test_sandbox_personas import _start, _switch

village = test_sandbox_http.village
pytestmark = pytest.mark.integration
_csrf = test_sandbox_http._csrf


def _prepared(village, client, *, delivery_method="email"):
    """Prepare a real September draft and switch to its administrator."""
    prefix, _ = _start(client, "operator")
    _policy(client, prefix, delivery_method=delivery_method)
    _import(client, prefix)
    assert _prepare(client, prefix).status_code == 200
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT id FROM billing_periods")
        period = cursor.fetchone()[0]
    _switch(client, prefix, "administrator")
    return prefix, period


def _issued(village, client, *, deliver=True, delivery_method="email"):
    """Issue the fictional invoices and optionally deliver them normally."""
    prefix, period = _prepared(village, client, delivery_method=delivery_method)
    page = client.get(prefix + COMMUNITY + "/billing")
    assert (
        client.post(
            prefix + COMMUNITY + f"/billing/period/{period}/approve",
            data={"csrf_token": _csrf(page), "confirm_approval": "yes"},
        ).status_code
        == 302
    )
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT participant_id,id FROM invoices")
        invoices = dict(cursor.fetchall())
    if deliver:
        for member in ("sonnwil-resident", "sonnwil-neighbour"):
            page = client.get(prefix + COMMUNITY + "/billing")
            assert (
                client.post(
                    prefix + COMMUNITY + f"/billing/invoice/{invoices[member]}/deliver",
                    data={"csrf_token": _csrf(page)},
                ).status_code
                == 302
            )
    return prefix, invoices


def _recreate_registration(client, prefix, replacement_email):
    """Delete and re-register the same building through ordinary confirmation links."""
    assert (
        client.post(
            prefix + "/unsubscribe", data={"email": "sonnwil-resident@example.org"}
        ).status_code
        == 200
    )
    assert client.post(_mail_link(client, prefix, "/unsubscribe/")).status_code == 200
    assert (
        client.post(
            prefix + "/api/register_full",
            json={
                "email": replacement_email,
                "profile": {
                    "building_id": "sonnwil-resident",
                    "address": "Musterweg 9, Sonnwil",
                    "lat": 47.1009,
                    "lon": 8.1009,
                    "annual_consumption_kwh": 9000,
                    "potential_pv_kwp": 0,
                    "bfs_number": 999999,
                },
            },
        ).status_code
        == 200
    )
    assert client.get(_mail_link(client, prefix, "/confirm/")).status_code == 200
    assert (
        client.post(
            prefix + "/dashboard/access/request",
            data={"email": replacement_email},
        ).status_code
        == 200
    )
    client.get(_mail_link(client, prefix, "/dashboard/access/"))
    assert client.get(prefix + "/dashboard/export").json["email"] == replacement_email


@pytest.mark.parametrize(
    "replacement_email", ["replacement@example.org", "sonnwil-resident@example.org"]
)
def test_recreated_household_cannot_read_previous_registration_invoice(
    village, replacement_email
):
    """A replacement registration cannot inherit retained invoices or their captured mail."""
    client = village.test_client()
    prefix, invoices = _issued(village, client)
    _switch(client, prefix, "resident")
    invoice = invoices["sonnwil-resident"]
    invoice_path = prefix + "/dashboard/invoices/" + str(invoice)
    detail = client.get(invoice_path)
    assert detail.status_code == 200 and "43.20" in detail.text
    assert client.get(invoice_path + "/pdf").data.startswith(b"%PDF-")
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT row_to_json(i) FROM invoices i ORDER BY id")
        snapshots = cursor.fetchall()
    profile = client.get(prefix + "/dashboard")
    assert (
        client.post(
            prefix + "/dashboard/profile",
            data={
                "csrf_token": _csrf(profile),
                "annual_consumption_kwh": "7200",
                "potential_pv_kwp": "0",
            },
        ).status_code
        == 302
    )
    assert "43.20" in client.get(invoice_path).text
    _recreate_registration(client, prefix, replacement_email)
    historical = client.get(invoice_path)
    assert historical.status_code == 404, (
        "replacement registration received the retained invoice"
    )
    assert client.get(invoice_path + "/pdf").status_code == 404
    assert invoice_path not in client.get(prefix + "/sandbox/inbox").text
    assert invoice_path not in client.get(prefix + "/dashboard/invoices").text
    _switch(client, prefix, "administrator")
    assert (
        "43.20"
        in client.get(prefix + "/leg/community/sonnwil-operating-leg/billing").text
    )
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT row_to_json(i) FROM invoices i ORDER BY id")
        assert cursor.fetchall() == snapshots


def test_legacy_unbound_invoice_stays_retained_without_member_access(village):
    """Legacy unbound invoices stay retained for operators without member access or delivery."""
    client = village.test_client()
    prefix, invoices = _issued(village, client, deliver=False)
    invoice = invoices["sonnwil-resident"]
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cursor,
    ):
        # Model an invoice issued before registration proofs existed. Only this
        # fixture setup bypasses the production immutability trigger.
        cursor.execute("ALTER TABLE invoices DISABLE TRIGGER invoices_immutable")
        cursor.execute(
            "UPDATE invoices SET provenance_snapshot=provenance_snapshot-'participant_registration_id' WHERE id=%s",
            (invoice,),
        )
        cursor.execute("ALTER TABLE invoices ENABLE TRIGGER invoices_immutable")
        cursor.execute("SELECT row_to_json(i) FROM invoices i WHERE id=%s", (invoice,))
        frozen = cursor.fetchone()[0]
    _switch(client, prefix, "resident")
    own = prefix + f"/dashboard/invoices/{invoice}"
    assert client.get(own).status_code == 404
    assert client.get(own + "/pdf").status_code == 404
    assert own not in client.get(prefix + "/dashboard/invoices").text
    _switch(client, prefix, "administrator")
    page = client.get(prefix + COMMUNITY + "/billing")
    assert "43.20" in page.text
    refused = client.post(
        prefix + COMMUNITY + f"/billing/invoice/{invoice}/deliver",
        data={"csrf_token": _csrf(page)},
    )
    assert refused.status_code == 409
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT row_to_json(i) FROM invoices i WHERE id=%s", (invoice,))
        assert cursor.fetchone()[0] == frozen
        cursor.execute(
            "SELECT COUNT(*) FROM invoice_delivery_jobs WHERE invoice_id=%s", (invoice,)
        )
        assert cursor.fetchone()[0] == 0


def test_changed_draft_registration_cannot_be_approved(village):
    """Approval refuses a draft whose original participant registration changed."""
    client = village.test_client()
    prefix, period = _prepared(village, client)
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE buildings SET referral_code='replacement-registration' WHERE building_id='sonnwil-resident'"
        )
    page = client.get(prefix + COMMUNITY + "/billing")
    refused = client.post(
        prefix + COMMUNITY + f"/billing/period/{period}/approve",
        data={"csrf_token": _csrf(page), "confirm_approval": "yes"},
    )
    assert refused.status_code == 409
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT status FROM billing_periods WHERE id=%s", (period,))
        assert cursor.fetchone()[0] == "draft"
        cursor.execute("SELECT COUNT(*) FROM invoices")
        assert cursor.fetchone()[0] == 0


@pytest.mark.parametrize("delivery_method", ["email", "download"])
def test_delivery_never_uses_a_replacement_registration_email(village, delivery_method):
    """Both delivery methods refuse a replacement registration with an explicit conflict."""
    client = village.test_client()
    prefix, invoices = _issued(
        village, client, deliver=False, delivery_method=delivery_method
    )
    invoice = invoices["sonnwil-resident"]
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE buildings SET referral_code='replacement-registration', email='replacement@example.org' WHERE building_id='sonnwil-resident'"
        )
    page = client.get(prefix + COMMUNITY + "/billing")
    refused = client.post(
        prefix + COMMUNITY + f"/billing/invoice/{invoice}/deliver",
        data={"csrf_token": _csrf(page)},
    )
    assert refused.status_code == 409
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT COUNT(*) FROM invoice_delivery_jobs WHERE invoice_id=%s", (invoice,)
        )
        assert cursor.fetchone()[0] == 0


@pytest.mark.parametrize("reconciliation", [None, "malformed"])
def test_malformed_draft_reconciliation_is_a_domain_conflict(village, reconciliation):
    """Malformed legacy evidence returns a conflict without issuing invoices."""
    import psycopg2.extras

    client = village.test_client()
    prefix, period = _prepared(village, client)
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE billing_periods SET reconciliation=%s WHERE id=%s",
            (psycopg2.extras.Json(reconciliation), period),
        )
    page = client.get(prefix + COMMUNITY + "/billing")
    refused = client.post(
        prefix + COMMUNITY + f"/billing/period/{period}/approve",
        data={"csrf_token": _csrf(page), "confirm_approval": "yes"},
    )
    assert refused.status_code == 409
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT COUNT(*) FROM invoices")
        assert cursor.fetchone()[0] == 0


def test_legacy_draft_can_be_prepared_again_without_withdrawing_source(
    village, monkeypatch
):
    """Initialization retains unbound drafts but frees their window for fresh preparation."""
    from store import schema

    client = village.test_client()
    prefix, period = _prepared(village, client)
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE billing_periods SET reconciliation=reconciliation-'participant_registrations' WHERE id=%s",
            (period,),
        )
        cursor.execute(
            "SELECT row_to_json(p) FROM billing_periods p WHERE id=%s", (period,)
        )
        frozen = cursor.fetchone()[0]
    with monkeypatch.context() as patch:
        patch.setattr(
            schema, "_get_connection", lambda: _workspace_connection(village, prefix)
        )
        schema.create_tables()
        schema.create_tables()
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT row_to_json(p) FROM billing_periods p WHERE id=%s", (period,)
        )
        retained = cursor.fetchone()[0]
        assert retained == {**frozen, "status": "invalidated"}
        cursor.execute("SELECT COUNT(*) FROM vnb_calculated_values_events")
        assert cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT COUNT(*) FROM vnb_calculated_values_deliveries WHERE status='accepted'"
        )
        assert cursor.fetchone()[0] == 1
    _switch(client, prefix, "operator")
    prepared = _prepare(client, prefix)
    assert prepared.status_code == 200
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT id FROM billing_periods WHERE status='draft'")
        replacement = cursor.fetchone()[0]
        assert replacement != period
    with monkeypatch.context() as patch:
        patch.setattr(
            schema, "_get_connection", lambda: _workspace_connection(village, prefix)
        )
        schema.create_tables()
    _switch(client, prefix, "administrator")
    page = client.get(prefix + COMMUNITY + "/billing")
    assert (
        client.post(
            prefix + COMMUNITY + f"/billing/period/{replacement}/approve",
            data={"csrf_token": _csrf(page), "confirm_approval": "yes"},
        ).status_code
        == 302
    )
