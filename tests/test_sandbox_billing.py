# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fictional invoices use normal source, policy, approval and member guards."""

import io
import re
from decimal import Decimal

import pytest

from tests import test_sandbox_http
from tests.test_sandbox_metering import _workspace_connection
from tests.test_sandbox_personas import _start, _switch

village = test_sandbox_http.village
_csrf = test_sandbox_http._csrf
pytestmark = pytest.mark.integration
COMMUNITY = "/leg/community/sonnwil-operating-leg"
POLICY = {
    "effective_from": "2026-09-01",
    "internal_price_rp": "12",
    "grid_fee_rp": "8",
    "settlement_fee_rp": "0",
    "network_level": "same",
    "distribution_model": "proportional",
    "vat_mode": "none",
    "vat_rate_pct": "",
    "payment_days": "30",
    "invoice_prefix": "SIMULATION",
    "delivery_method": "email",
}


def _policy(client, prefix, **changes):
    """Save the September policy through the ordinary billing form."""
    page = client.get(prefix + COMMUNITY + "/billing-policy")
    response = client.post(
        prefix + COMMUNITY + "/billing-policy",
        data={**POLICY, **changes, "csrf_token": _csrf(page)},
    )
    assert response.status_code == 302


def _import(client, prefix):
    """Import the complete synthetic September metering source."""
    path = prefix + "/sandbox/metering"
    page = client.get(path)
    delivery = client.get(path + "/fixtures/valid.xml.gz")
    response = client.post(
        path + "/import",
        data={
            "csrf_token": _csrf(page),
            "delivery": (io.BytesIO(delivery.data), "simulation-sonnwil.xml.gz"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 303


def _prepare(client, prefix):
    """Confirm draft preparation using the current page's CSRF token."""
    page = client.get(prefix + "/sandbox/billing")
    assert page.status_code == 200
    return client.post(
        prefix + "/sandbox/billing/prepare",
        data={"csrf_token": _csrf(page), "confirm": "yes"},
        follow_redirects=True,
    )


def test_draft_requires_policy_and_evidence_then_issues_independent_totals(village):
    """Billing requires evidence and policy, then preserves independently checked totals."""
    client = village.test_client()
    prefix, _ = _start(client, "operator")
    missing_policy = _prepare(client, prefix)
    assert missing_policy.status_code == 409
    assert "billing_tariff_missing" in missing_policy.text
    _policy(client, prefix)
    missing_source = _prepare(client, prefix)
    assert missing_source.status_code == 409
    assert "vnb_evidence_missing" in missing_source.text
    _import(client, prefix)
    prepared = _prepare(client, prefix)
    assert prepared.status_code == 200
    assert "1440" in prepared.text and "43.20" in prepared.text
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cur,
    ):
        cur.execute("SELECT id, status, prepared_by FROM billing_periods")
        period, status, actor = cur.fetchone()
        assert status == "draft" and actor == "sonnwil-operator"
    approval = prefix + COMMUNITY + f"/billing/period/{period}/approve"
    fields = {"csrf_token": _csrf(prepared), "confirm_approval": "yes"}
    assert client.post(approval, data=fields).status_code == 403
    _switch(client, prefix, "administrator")
    workspace = client.get(prefix + COMMUNITY + "/billing")
    fields["csrf_token"] = _csrf(workspace)
    for _ in range(2):
        assert client.post(approval, data=fields).status_code == 302
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cur,
    ):
        cur.execute(
            "SELECT participant_id, gross_chf FROM invoices ORDER BY participant_id"
        )
        assert cur.fetchall() == [
            ("sonnwil-neighbour", Decimal("43.20")),
            ("sonnwil-operator", Decimal("43.20")),
            ("sonnwil-producer", Decimal("-129.60")),
            ("sonnwil-resident", Decimal("43.20")),
        ]
        cur.execute(
            "SELECT id, participant_id, invoice_number, policy_snapshot, line_items_snapshot, provenance_snapshot, gross_chf FROM invoices ORDER BY id"
        )
        frozen = cur.fetchall()
    workspace = client.get(prefix + COMMUNITY + "/billing")
    for row in frozen:
        endpoint = prefix + COMMUNITY + f"/billing/invoice/{row[0]}/deliver"
        for _ in range(2):
            assert (
                client.post(endpoint, data={"csrf_token": _csrf(workspace)}).status_code
                == 302
            )
    # Administrative delivery does not grant access to member-only invoices.
    resident = next(row for row in frozen if row[1] == "sonnwil-resident")
    producer = next(row for row in frozen if row[1] == "sonnwil-producer")
    _switch(client, prefix, "resident")
    inbox = client.get(prefix + "/sandbox/inbox")
    assert resident[2] in inbox.text and producer[2] not in inbox.text
    assert inbox.text.count(resident[2]) == 1
    own = prefix + f"/dashboard/invoices/{resident[0]}"
    detail = client.get(own)
    assert detail.status_code == 200 and "SIMULATION SONNWIL" in detail.text
    assert "43.20" in detail.text and "172.80" not in detail.text
    pdf = client.get(own + "/pdf")
    assert (
        pdf.status_code == 200 and pdf.data.startswith(b"%PDF") and len(pdf.data) > 5000
    )
    assert client.get(prefix + f"/dashboard/invoices/{producer[0]}").status_code == 404
    assert (
        client.get(prefix + f"/dashboard/invoices/{producer[0]}/pdf").status_code == 404
    )
    exported = client.get(prefix + "/dashboard/export")
    assert exported.json["simulation"].startswith("SIMULATION SONNWIL")
    other = village.test_client()
    other_prefix, _ = _start(other, "resident")
    assert (
        other.get(other_prefix + f"/dashboard/invoices/{resident[0]}").status_code
        == 404
    )
    assert other.get(own).status_code == 410
    assert resident[2] not in other.get(other_prefix + "/sandbox/inbox").text
    _switch(client, prefix, "administrator")
    archive = client.get(prefix + COMMUNITY + "/archive")
    assert archive.status_code == 200
    assert archive.json["manifest"]["simulation"].startswith("SIMULATION SONNWIL")
    _policy(client, prefix, effective_from="2026-11-01", internal_price_rp="19")
    workspace = client.get(prefix + COMMUNITY + "/billing")
    cancel = prefix + COMMUNITY + f"/billing/invoice/{resident[0]}/cancel"
    assert client.post(cancel, data={"csrf_token": _csrf(workspace)}).status_code == 409
    fields = {
        "csrf_token": _csrf(workspace),
        "reason": "SIMULATION falscher Übungstarif",
    }
    for _ in range(2):
        assert client.post(cancel, data=fields).status_code == 302
    # A different member's invoice is never an eligible replacement.
    assert (
        client.post(
            prefix + COMMUNITY + f"/billing/invoice/{resident[0]}/correct",
            data={**fields, "corrected_invoice_id": producer[0]},
        ).status_code
        == 409
    )
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cur,
    ):
        cur.execute(
            "SELECT id, participant_id, invoice_number, policy_snapshot, line_items_snapshot, provenance_snapshot, gross_chf FROM invoices ORDER BY id"
        )
        assert cur.fetchall() == frozen
        cur.execute(
            "SELECT new_state FROM invoice_lifecycle_events WHERE invoice_id=%s ORDER BY id",
            (resident[0],),
        )
        assert [row[0] for row in cur.fetchall()] == ["delivered", "cancelled"]
    _switch(client, prefix, "resident")
    cancelled = client.get(own)
    assert (
        cancelled.status_code == 200
        and "Storniert" in cancelled.text
        and "43.20" in cancelled.text
    )


@pytest.mark.parametrize(
    "fault", ["withdrawn", "replacement", "changed", "unequal", "battery", "invalid"]
)
def test_ordinary_evidence_guards_cannot_be_bypassed_in_the_demo(village, fault):
    """The demo retains ordinary evidence, allocation and issued-draft guards."""
    client = village.test_client()
    prefix, _ = _start(client, "operator")
    _policy(client, prefix)
    if fault in {"unequal", "invalid"}:
        path = prefix + "/sandbox/metering"
        page = client.get(path)
        fixture_path = (
            prefix + "/sandbox/billing/fixtures/unequal.xml.gz"
            if fault == "unequal"
            else path + "/fixtures/missing.xml.gz"
        )
        source = client.get(fixture_path)
        assert source.status_code == 200
        imported = client.post(
            path + "/import",
            data={
                "csrf_token": _csrf(page),
                "delivery": (io.BytesIO(source.data), "simulation-source.xml.gz"),
            },
            content_type="multipart/form-data",
        )
        assert imported.status_code == (303 if fault == "unequal" else 422)
    else:
        _import(client, prefix)
    if fault in {"withdrawn", "replacement"}:
        assert _prepare(client, prefix).status_code == 200
        with (
            _workspace_connection(village, prefix) as connection,
            connection.cursor() as cur,
        ):
            cur.execute("SELECT id FROM vnb_calculated_values_deliveries")
            delivery = cur.fetchone()[0]
        page = client.get(prefix + "/sandbox/metering")
        assert (
            client.post(
                prefix + f"/sandbox/metering/withdraw/{delivery}",
                data={
                    "csrf_token": _csrf(page),
                    "confirm": "yes",
                    "reason": "SIMULATION Quellenprüfung",
                },
            ).status_code
            == 303
        )
        if fault == "replacement":
            path = prefix + "/sandbox/metering"
            page = client.get(path)
            corrected = client.get(path + "/fixtures/corrected.xml.gz")
            assert (
                client.post(
                    path + "/import",
                    data={
                        "csrf_token": _csrf(page),
                        "delivery": (
                            io.BytesIO(corrected.data),
                            "simulation-corrected.xml.gz",
                        ),
                    },
                    content_type="multipart/form-data",
                ).status_code
                == 303
            )
    elif fault == "changed":
        # Deliberately damaged physical storage must not be blessed by the UI.
        with (
            _workspace_connection(village, prefix) as connection,
            connection.cursor() as cur,
        ):
            cur.execute(
                "UPDATE metering_point_readings SET total_kwh=total_kwh+1 WHERE metering_point_id='SONNWIL-SIM-C1'"
            )
    elif fault == "battery":
        page = client.get(prefix + "/leg/dashboard?cid=sonnwil-operating-leg")
        assert (
            client.post(
                prefix + COMMUNITY + "/battery",
                data={
                    "csrf_token": _csrf(page),
                    "participant_id": "sonnwil-unmapped-battery",
                    "capacity_kwh": "20",
                    "annual_cost_chf": "100",
                    **{
                        "share:" + name: "25"
                        for name in (
                            "sonnwil-resident",
                            "sonnwil-producer",
                            "sonnwil-operator",
                            "sonnwil-neighbour",
                        )
                    },
                },
            ).status_code
            == 302
        )
    if fault == "replacement":
        _switch(client, prefix, "administrator")
        with (
            _workspace_connection(village, prefix) as connection,
            connection.cursor() as cur,
        ):
            cur.execute("SELECT id, status, input_fingerprint FROM billing_periods")
            original, status, fingerprint = cur.fetchone()
            assert status == "invalidated"
        page = client.get(prefix + COMMUNITY + "/billing")
        assert (
            client.post(
                prefix + COMMUNITY + f"/billing/period/{original}/approve",
                data={"csrf_token": _csrf(page), "confirm_approval": "yes"},
            ).status_code
            == 409
        )
        _switch(client, prefix, "operator")
        assert _prepare(client, prefix).status_code == 200
        with (
            _workspace_connection(village, prefix) as connection,
            connection.cursor() as cur,
        ):
            cur.execute(
                "SELECT id, status, input_fingerprint FROM billing_periods ORDER BY id"
            )
            rows = cur.fetchall()
            assert len(rows) == 2 and rows[0] == (original, "invalidated", fingerprint)
            replacement = rows[1][0]
        _switch(client, prefix, "administrator")
        page = client.get(prefix + COMMUNITY + "/billing")
        assert (
            client.post(
                prefix + COMMUNITY + f"/billing/period/{replacement}/approve",
                data={"csrf_token": _csrf(page), "confirm_approval": "yes"},
            ).status_code
            == 302
        )
        with (
            _workspace_connection(village, prefix) as connection,
            connection.cursor() as cur,
        ):
            cur.execute(
                "SELECT participant_id, gross_chf FROM invoices ORDER BY participant_id"
            )
            assert cur.fetchall() == [
                ("sonnwil-neighbour", Decimal("51.84")),
                ("sonnwil-operator", Decimal("51.84")),
                ("sonnwil-producer", Decimal("-155.52")),
                ("sonnwil-resident", Decimal("51.84")),
            ]
        return
    refused = _prepare(client, prefix)
    assert refused.status_code == 409
    assert {
        "withdrawn": "vnb_evidence_missing",
        "replacement": "billing_inputs_changed",
        "changed": "vnb_allocation_mismatch",
        "unequal": "vnb_allocation_mismatch",
        "battery": "billing_run_failed",
        "invalid": "vnb_evidence_missing",
    }[fault] in refused.text, re.findall(
        r"(?:billing|vnb|metering|battery)_[a-z_]+", refused.text
    )
    _switch(client, prefix, "administrator")
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cur,
    ):
        cur.execute("SELECT id FROM billing_periods")
        periods = cur.fetchall()
    for (period,) in periods:
        page = client.get(prefix + COMMUNITY + "/billing")
        assert (
            client.post(
                prefix + COMMUNITY + f"/billing/period/{period}/approve",
                data={"csrf_token": _csrf(page), "confirm_approval": "yes"},
            ).status_code
            == 409
        )
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cur,
    ):
        cur.execute("SELECT COUNT(*) FROM invoices")
        assert cur.fetchone()[0] == 0


def test_billing_controls_require_current_role_and_csrf_and_stay_worker_only(
    village, monkeypatch
):
    """Billing controls enforce current roles, CSRF and worker confinement."""
    client = village.test_client()
    prefix, _ = _start(client, "operator")
    path = prefix + "/sandbox/billing"
    page = client.get(path)
    assert client.post(path + "/prepare", data={"confirm": "yes"}).status_code == 400
    assert (
        client.post(path + "/prepare", data={"csrf_token": _csrf(page)}).status_code
        == 400
    )
    for role in ("resident", "producer", "founder", "municipality", "utility"):
        _switch(client, prefix, role)
        denied = 401 if role in {"municipality", "utility"} else 403
        assert client.get(path).status_code == denied
        assert client.get(path + "/fixtures/unequal.xml.gz").status_code == denied
    _switch(client, prefix, "resident")
    resident = client.get(prefix + "/dashboard")
    assert (
        client.post(
            path + "/prepare", data={"csrf_token": _csrf(resident), "confirm": "yes"}
        ).status_code
        == 403
    )
    from flask import Flask

    import sandbox_billing

    monkeypatch.delenv("OPENLEG_SANDBOX_WORKER", raising=False)
    with pytest.raises(RuntimeError, match="requires a village worker"):
        sandbox_billing.install(Flask(__name__))


def test_persistent_billing_read_failure_returns_retryable_unavailable_state(village):
    """Persistent storage failure renders a retryable unavailable state."""
    client = village.test_client()
    prefix, _ = _start(client, "operator")
    path = prefix + "/sandbox/billing"
    page = client.get(path)
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cur,
    ):
        cur.execute("ALTER TABLE billing_tariffs RENAME TO simulation_hidden_tariffs")
    try:
        for response in (
            client.get(path),
            client.post(
                path + "/prepare", data={"csrf_token": _csrf(page), "confirm": "yes"}
            ),
        ):
            assert response.status_code == 503
            assert "billing_store_unavailable" in response.text
    finally:
        with (
            _workspace_connection(village, prefix) as connection,
            connection.cursor() as cur,
        ):
            cur.execute(
                "ALTER TABLE simulation_hidden_tariffs RENAME TO billing_tariffs"
            )
    assert client.get(path).status_code == 200
