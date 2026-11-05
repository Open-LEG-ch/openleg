# SPDX-License-Identifier: AGPL-3.0-or-later
"""Private question and attachment history keeps its original registration."""

import io

import pytest

from tests import test_sandbox_http
from tests.test_retained_invoice_identity_postgres import (
    _issued,
    _recreate_registration,
)
from tests.test_sandbox_metering import _workspace_connection
from tests.test_sandbox_personas import _switch

village = test_sandbox_http.village
pytestmark = pytest.mark.integration
_csrf = test_sandbox_http._csrf


@pytest.mark.parametrize(
    "replacement_email", ["replacement@example.org", "sonnwil-resident@example.org"]
)
def test_recreated_household_cannot_read_previous_registration_question(
    village, replacement_email
):
    """Replacement registrations cannot inherit retained questions or attachments."""
    client = village.test_client()
    prefix, invoices = _issued(village, client)
    _switch(client, prefix, "resident")
    invoice = invoices["sonnwil-resident"]
    invoice_path = prefix + "/dashboard/invoices/" + str(invoice)
    detail = client.get(invoice_path)
    assert detail.status_code == 200 and "43.20" in detail.text
    assert client.get(invoice_path + "/pdf").data.startswith(b"%PDF-")
    assert (
        client.post(
            invoice_path + "/queries",
            data={
                "csrf_token": _csrf(detail),
                "category": "amount",
                "message": "Original registration private question",
                "attachment": (
                    io.BytesIO(b"%PDF-1.4\nprivate original\n%%EOF"),
                    "original.pdf",
                    "application/pdf",
                ),
            },
            content_type="multipart/form-data",
        ).status_code
        == 302
    )
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT id FROM invoice_queries WHERE invoice_id=%s", (invoice,))
        query = cursor.fetchone()[0]
        cursor.execute(
            "SELECT id FROM invoice_query_messages WHERE query_id=%s", (query,)
        )
        message = cursor.fetchone()[0]
        cursor.execute("SELECT row_to_json(i) FROM invoices i ORDER BY id")
        snapshots = cursor.fetchall()
    attachment = prefix + "/sandbox/questions/attachments/" + str(message)
    assert client.get(attachment).data.startswith(b"%PDF-")
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
    assert (
        "Original registration private question"
        not in client.get(prefix + "/sandbox/questions").text
    )
    assert client.get(attachment).status_code == 404
    profile = client.get(prefix + "/dashboard")
    assert (
        client.post(
            invoice_path + "/queries",
            data={
                "csrf_token": _csrf(profile),
                "category": "amount",
                "message": "Not the original registration",
            },
        ).status_code
        == 400
    )
    assert (
        client.post(
            invoice_path + f"/queries/{query}/reply",
            data={
                "csrf_token": _csrf(profile),
                "message": "Not the original registration",
            },
        ).status_code
        == 400
    )
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
        cursor.execute(
            "SELECT COUNT(*) FROM invoice_query_messages WHERE query_id=%s", (query,)
        )
        assert cursor.fetchone() == (1,)
