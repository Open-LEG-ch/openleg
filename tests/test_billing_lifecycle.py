# SPDX-License-Identifier: AGPL-3.0-or-later
"""Acceptance contract for invoice delivery and audit lifecycle (#401)."""

from contextlib import contextmanager
from datetime import date
from unittest.mock import MagicMock

import pytest

from tests.test_dashboard_access_routes import _set_session
from tests.test_dashboard_access_routes import (
    app_module as dashboard_app_module,  # noqa: F401
)

COMMUNITY = "community-a"
INVOICE_URL = f"/leg/community/{COMMUNITY}/billing/invoice/42"


def test_invoice_lifecycle_allows_only_the_auditable_happy_path():
    import billing_lifecycle

    assert billing_lifecycle.next_state("issued", "deliver") == "delivered"
    assert (
        billing_lifecycle.next_state(
            "delivered", "pay", reference="BANK-42", effective_date=date(2026, 9, 1)
        )
        == "paid"
    )
    assert (
        billing_lifecycle.next_state("issued", "cancel", reason="Falscher Tarif")
        == "cancelled"
    )
    assert (
        billing_lifecycle.next_state(
            "cancelled", "correct", reason="Neuer Abrechnungslauf"
        )
        == "corrected"
    )


@pytest.mark.parametrize(
    ("state", "event", "kwargs"),
    [
        (
            "issued",
            "pay",
            {"reference": "BANK-42", "effective_date": date(2026, 9, 1)},
        ),
        ("paid", "cancel", {"reason": "zu spät"}),
        ("delivered", "correct", {"reason": "ohne Storno"}),
        ("cancelled", "deliver", {}),
        ("issued", "cancel", {"reason": ""}),
        (
            "delivered",
            "pay",
            {"reference": "", "effective_date": date(2026, 9, 1)},
        ),
        ("delivered", "pay", {"reference": "BANK-42", "effective_date": None}),
    ],
)
def test_invoice_lifecycle_refuses_invalid_or_incomplete_transitions(
    state, event, kwargs
):
    import billing_lifecycle

    with pytest.raises(billing_lifecycle.InvoiceLifecycleError):
        billing_lifecycle.next_state(state, event, **kwargs)


def test_invoice_lifecycle_describes_all_member_states_in_swiss_german():
    import billing_lifecycle

    assert billing_lifecycle.STATE_LABELS == {
        "issued": "Freigegeben",
        "delivered": "Zugestellt",
        "paid": "Bezahlt",
        "cancelled": "Storniert",
        "corrected": "Korrigiert",
    }
    assert "ß" not in " ".join(billing_lifecycle.STATE_LABELS.values())


def test_member_routes_render_all_states_and_correction_links(
    dashboard_app_module,  # noqa: F811
    monkeypatch,
):
    from tests.test_billing_member_invoices import DETAIL_VIEW

    labels = ["Freigegeben", "Zugestellt", "Bezahlt", "Storniert", "Korrigiert"]
    monkeypatch.setattr(
        dashboard_app_module.dashboard_module,
        "member_invoices_view",
        MagicMock(
            return_value={
                "invoices": [
                    {
                        "id": index,
                        "invoice_number": f"LEG-2026-{index:06d}",
                        "period_label": "Januar 2026",
                        "status_label": label,
                        "display_gross_chf": "12.00",
                        "due_date": "2026-03-01",
                    }
                    for index, label in enumerate(labels, start=1)
                ]
            }
        ),
    )
    detail = {
        **DETAIL_VIEW,
        "status_label": "Korrigiert",
        "corrects_invoice_number": "LEG-2026-000040",
        "corrected_by_invoice_number": "LEG-2026-000042",
    }
    monkeypatch.setattr(
        dashboard_app_module.dashboard_module,
        "member_invoice_detail",
        MagicMock(return_value=detail),
    )
    client = dashboard_app_module.web.test_client()
    _set_session(client)

    listing = client.get("/dashboard/invoices").get_data(as_text=True)
    rendered_detail = client.get("/dashboard/invoices/41").get_data(as_text=True)

    assert all(label in listing for label in labels)
    assert "Korrektur zu:" in rendered_detail
    assert "LEG-2026-000040" in rendered_detail
    assert "Korrigiert durch:" in rendered_detail
    assert "LEG-2026-000042" in rendered_detail


def test_admin_workspace_renders_every_lifecycle_control(
    dashboard_app_module,  # noqa: F811
    monkeypatch,
):
    base = {
        "participant_id": "member-building",
        "display_gross_chf": "12.00",
        "display_unit_price_rp": "15.00 Rp./kWh",
        "delivery_method_label": "E-Mail",
        "events": [],
        "correction_candidates": [],
        "corrects_invoice_number": None,
        "corrected_by_invoice_number": None,
        "delivery_job_status": None,
    }
    invoices = [
        {
            **base,
            "id": 1,
            "invoice_number": "LEG-1",
            "lifecycle_state": "issued",
            "status_label": "Freigegeben",
        },
        {
            **base,
            "id": 2,
            "invoice_number": "LEG-2",
            "lifecycle_state": "delivered",
            "status_label": "Zugestellt",
        },
        {
            **base,
            "id": 3,
            "invoice_number": "LEG-3",
            "lifecycle_state": "cancelled",
            "status_label": "Storniert",
            "correction_candidates": [{"id": 4, "invoice_number": "LEG-4"}],
        },
        {
            **base,
            "id": 5,
            "invoice_number": "LEG-5",
            "lifecycle_state": "issued",
            "status_label": "Freigegeben",
            "delivery_job_status": "pending",
        },
    ]
    monkeypatch.setattr(
        dashboard_app_module.dashboard_module,
        "leg_billing_workspace_view",
        MagicMock(
            return_value={
                "error": None,
                "community_id": COMMUNITY,
                "periods": [],
                "invoices": invoices,
                "billing_approved": False,
                "approval_error": None,
            }
        ),
    )
    client = dashboard_app_module.web.test_client()
    _set_session(client, building_id="admin-building")

    response = client.get(f"/leg/community/{COMMUNITY}/billing")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    for action in ("deliver", "delivery-confirmed", "paid", "cancel", "correct"):
        assert "/invoice/" in html and f"/{action}" in html
    for text in (
        "Zustellen",
        "Zahlung erfassen",
        "Stornieren",
        "Korrektur",
        "Zustellart",
        "Interner Preis",
        "15.00 Rp./kWh",
        "Audit-Verlauf",
    ):
        assert text in html


def _confirmed_admin(monkeypatch, dashboard):
    monkeypatch.setattr(
        dashboard,
        "_require_confirmed_admin",
        MagicMock(return_value={"building_id": "admin-building"}),
    )


def test_email_delivery_uses_existing_boundary_once_and_completes_audit(monkeypatch):
    import dashboard

    _confirmed_admin(monkeypatch, dashboard)
    prepare = MagicMock(
        return_value={
            "already_delivered": False,
            "delivery_method": "email",
            "recipient_email": "member@example.ch",
            "invoice_number": "LEG-2026-000001",
        }
    )
    complete = MagicMock(return_value={"lifecycle_state": "delivered"})
    monkeypatch.setattr(
        dashboard.db, "prepare_invoice_delivery", prepare, raising=False
    )
    monkeypatch.setattr(
        dashboard.db, "complete_invoice_delivery", complete, raising=False
    )
    monkeypatch.setattr(
        dashboard.db, "fail_invoice_delivery", MagicMock(), raising=False
    )
    send = MagicMock(return_value=True)

    result = dashboard.leg_deliver_invoice(
        COMMUNITY,
        "admin-building",
        42,
        send_email=send,
        invoice_url="https://openleg.ch/dashboard/invoices/42",
    )

    assert result["error"] is None
    send.assert_called_once()
    assert send.call_args.args[0] == "member@example.ch"
    assert "https://openleg.ch/dashboard/invoices/42" in send.call_args.args[2]
    complete.assert_called_once_with(42, COMMUNITY, "admin-building")


def test_delivery_retry_does_not_send_a_duplicate(monkeypatch):
    import dashboard

    _confirmed_admin(monkeypatch, dashboard)
    monkeypatch.setattr(
        dashboard.db,
        "prepare_invoice_delivery",
        MagicMock(return_value={"already_delivered": True}),
        raising=False,
    )
    send = MagicMock(return_value=True)

    result = dashboard.leg_deliver_invoice(
        COMMUNITY,
        "admin-building",
        42,
        send_email=send,
        invoice_url="https://openleg.ch/dashboard/invoices/42",
    )

    assert result == {"error": None, "already_delivered": True}
    send.assert_not_called()


def test_interrupted_delivery_retry_requires_confirmation_without_duplicate(
    monkeypatch,
):
    import dashboard

    _confirmed_admin(monkeypatch, dashboard)
    monkeypatch.setattr(
        dashboard.db,
        "prepare_invoice_delivery",
        MagicMock(
            return_value={
                "confirmation_required": True,
                "lifecycle_state": "issued",
            }
        ),
        raising=False,
    )
    send = MagicMock(return_value=True)

    result = dashboard.leg_deliver_invoice(
        COMMUNITY,
        "admin-building",
        42,
        send_email=send,
        invoice_url="https://openleg.ch/dashboard/invoices/42",
    )

    assert result["confirmation_required"] is True
    send.assert_not_called()


def test_admin_can_confirm_an_uncertain_delivery(monkeypatch):
    import dashboard

    _confirmed_admin(monkeypatch, dashboard)
    confirm = MagicMock(return_value={"lifecycle_state": "delivered"})
    monkeypatch.setattr(
        dashboard.db, "confirm_invoice_delivery", confirm, raising=False
    )

    result = dashboard.leg_confirm_invoice_delivery(COMMUNITY, "admin-building", 42)

    assert result == {"error": None, "lifecycle_state": "delivered"}
    confirm.assert_called_once_with(42, COMMUNITY, "admin-building")


def test_external_email_failure_is_recorded_without_marking_delivered(monkeypatch):
    import dashboard

    _confirmed_admin(monkeypatch, dashboard)
    monkeypatch.setattr(
        dashboard.db,
        "prepare_invoice_delivery",
        MagicMock(
            return_value={
                "already_delivered": False,
                "delivery_method": "email",
                "recipient_email": "member@example.ch",
                "invoice_number": "LEG-2026-000001",
            }
        ),
        raising=False,
    )
    fail = MagicMock()
    complete = MagicMock()
    monkeypatch.setattr(dashboard.db, "fail_invoice_delivery", fail, raising=False)
    monkeypatch.setattr(
        dashboard.db, "complete_invoice_delivery", complete, raising=False
    )

    result = dashboard.leg_deliver_invoice(
        COMMUNITY,
        "admin-building",
        42,
        send_email=MagicMock(return_value=False),
        invoice_url="https://openleg.ch/dashboard/invoices/42",
    )

    assert result["error"]
    fail.assert_called_once_with(
        42, COMMUNITY, "admin-building", "E-Mail-Versand fehlgeschlagen"
    )
    complete.assert_not_called()


def test_portal_delivery_never_calls_email(monkeypatch):
    import dashboard

    _confirmed_admin(monkeypatch, dashboard)
    monkeypatch.setattr(
        dashboard.db,
        "prepare_invoice_delivery",
        MagicMock(
            return_value={
                "already_delivered": False,
                "delivery_method": "download",
                "invoice_number": "LEG-2026-000001",
            }
        ),
        raising=False,
    )
    complete = MagicMock(return_value={"lifecycle_state": "delivered"})
    monkeypatch.setattr(
        dashboard.db, "complete_invoice_delivery", complete, raising=False
    )
    send = MagicMock()

    result = dashboard.leg_deliver_invoice(
        COMMUNITY,
        "admin-building",
        42,
        send_email=send,
        invoice_url="https://openleg.ch/dashboard/invoices/42",
    )

    assert result["error"] is None
    send.assert_not_called()
    complete.assert_called_once()


@pytest.mark.parametrize(
    "suffix", ["deliver", "delivery-confirmed", "paid", "cancel", "correct"]
)
def test_invoice_mutation_routes_require_dashboard_auth(
    dashboard_app_module,  # noqa: F811
    suffix,
):
    client = dashboard_app_module.web.test_client()
    response = client.post(f"{INVOICE_URL}/{suffix}")
    assert response.status_code == 401


def test_payment_route_passes_actor_date_and_reference(
    dashboard_app_module,  # noqa: F811
    monkeypatch,
):
    record = MagicMock(return_value={"error": None})
    monkeypatch.setattr(
        dashboard_app_module.dashboard_module,
        "leg_record_invoice_payment",
        record,
        raising=False,
    )
    client = dashboard_app_module.web.test_client()
    _set_session(client, building_id="admin-building")

    response = client.post(
        f"{INVOICE_URL}/paid",
        data={
            "csrf_token": "csrf-secret",
            "paid_date": "2026-09-01",
            "reference": "BANK-42",
        },
    )

    assert response.status_code == 302
    record.assert_called_once_with(
        COMMUNITY, "admin-building", 42, "2026-09-01", "BANK-42"
    )


def test_invoice_mutation_route_requires_csrf(
    dashboard_app_module,  # noqa: F811
    monkeypatch,
):
    deliver = MagicMock(return_value={"error": None})
    monkeypatch.setattr(
        dashboard_app_module.dashboard_module,
        "leg_deliver_invoice",
        deliver,
        raising=False,
    )
    client = dashboard_app_module.web.test_client()
    _set_session(client, building_id="admin-building")

    response = client.post(f"{INVOICE_URL}/deliver")

    assert response.status_code == 400
    deliver.assert_not_called()


def test_invalid_transition_is_private_conflict(
    dashboard_app_module,  # noqa: F811
    monkeypatch,
):
    import dashboard

    monkeypatch.setattr(
        dashboard_app_module.dashboard_module,
        "leg_cancel_invoice",
        MagicMock(side_effect=dashboard.InvoiceLifecycleError("internal detail")),
        raising=False,
    )
    monkeypatch.setattr(
        dashboard_app_module.dashboard_module,
        "leg_billing_workspace_view",
        MagicMock(
            return_value={
                "error": None,
                "community_id": COMMUNITY,
                "periods": [],
                "invoices": [],
                "billing_approved": False,
                "approval_error": None,
            }
        ),
        raising=False,
    )
    client = dashboard_app_module.web.test_client()
    _set_session(client, building_id="admin-building")

    response = client.post(
        f"{INVOICE_URL}/cancel",
        data={"csrf_token": "csrf-secret", "reason": "Zu spät"},
    )

    assert response.status_code == 409
    assert "internal detail" not in response.get_data(as_text=True)
    assert "no-store" in response.headers["Cache-Control"]


def test_email_failure_returns_private_bad_gateway(
    dashboard_app_module,  # noqa: F811
    monkeypatch,
):
    monkeypatch.setattr(
        dashboard_app_module.dashboard_module,
        "leg_deliver_invoice",
        MagicMock(return_value={"error": "E-Mail fehlgeschlagen."}),
        raising=False,
    )
    monkeypatch.setattr(
        dashboard_app_module.dashboard_module,
        "leg_billing_workspace_view",
        MagicMock(
            return_value={
                "error": None,
                "community_id": COMMUNITY,
                "periods": [],
                "invoices": [],
                "billing_approved": False,
                "approval_error": None,
            }
        ),
        raising=False,
    )
    client = dashboard_app_module.web.test_client()
    _set_session(client, building_id="admin-building")

    response = client.post(f"{INVOICE_URL}/deliver", data={"csrf_token": "csrf-secret"})

    assert response.status_code == 502
    assert "E-Mail fehlgeschlagen." in response.get_data(as_text=True)
    assert "no-store" in response.headers["Cache-Control"]


def test_workspace_loads_complete_audit_history(monkeypatch):
    import dashboard

    _confirmed_admin(monkeypatch, dashboard)
    monkeypatch.setattr(
        dashboard.db, "list_community_billing_periods", MagicMock(return_value=[])
    )
    monkeypatch.setattr(
        dashboard.db,
        "list_community_invoices",
        MagicMock(
            return_value=[
                {
                    "id": 42,
                    "participant_id": "member-building",
                    "invoice_number": "LEG-2026-000001",
                    "gross_chf": 12,
                    "lifecycle_state": "delivered",
                    "policy_snapshot": {
                        "delivery_method": "email",
                        "internal_price_chf_per_kwh": "0.150000",
                    },
                }
            ]
        ),
    )
    events = MagicMock(
        return_value=[
            {
                "invoice_id": 42,
                "actor_id": "admin-building",
                "previous_state": "issued",
                "new_state": "delivered",
                "reason": None,
                "reference": None,
            }
        ]
    )
    monkeypatch.setattr(dashboard.db, "list_community_invoice_events", events)

    view = dashboard.leg_billing_workspace_view(COMMUNITY, "admin-building")

    events.assert_called_once_with(COMMUNITY)
    assert view["invoices"][0]["events"][0]["previous_status_label"] == "Freigegeben"
    assert view["invoices"][0]["events"][0]["new_status_label"] == "Zugestellt"
    assert view["invoices"][0]["delivery_method_label"] == "E-Mail"
    assert view["invoices"][0]["display_unit_price_rp"] == "15.00 Rp./kWh"


def test_workspace_shows_nicht_angegangen_when_the_frozen_price_is_missing(monkeypatch):
    """An invoice whose frozen snapshot carries no readable internal price is
    shown as "Nicht angegeben", never as an invented tariff."""
    import dashboard

    _confirmed_admin(monkeypatch, dashboard)
    monkeypatch.setattr(
        dashboard.db, "list_community_billing_periods", MagicMock(return_value=[])
    )
    monkeypatch.setattr(
        dashboard.db,
        "list_community_invoices",
        MagicMock(
            return_value=[
                {
                    "id": 43,
                    "participant_id": "member-building",
                    "invoice_number": "LEG-2026-000002",
                    "gross_chf": 12,
                    "lifecycle_state": "issued",
                    "policy_snapshot": {"delivery_method": "email"},
                }
            ]
        ),
    )
    monkeypatch.setattr(
        dashboard.db, "list_community_invoice_events", MagicMock(return_value=[])
    )

    view = dashboard.leg_billing_workspace_view(COMMUNITY, "admin-building")

    assert view["invoices"][0]["display_unit_price_rp"] == "Nicht angegeben"


class _LifecycleCursor:
    def __init__(self, *, ones=(), rows=()):
        self.ones = list(ones)
        self.rows = list(rows)
        self.executed = []
        self.rowcount = 1

    def execute(self, query, params=None):
        self.executed.append((" ".join(query.split()), params))

    def fetchone(self):
        return self.ones.pop(0) if self.ones else None

    def fetchall(self):
        return self.rows.pop(0) if self.rows else []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _connection(cursor):
    class Connection:
        def cursor(self):
            return cursor

    @contextmanager
    def factory():
        yield Connection()

    return factory


def _issued_invoice(method="email"):
    return {
        "id": 42,
        "community_id": COMMUNITY,
        "participant_id": "member-building",
        "invoice_number": "LEG-2026-000001",
        "recipient_email": "member@example.ch",
        "policy_snapshot": {"delivery_method": method},
    }


def test_store_reserves_delivery_once_before_external_effect(monkeypatch):
    import database
    from store import billing

    cursor = _LifecycleCursor(ones=[_issued_invoice(), None, None])
    monkeypatch.setattr(database, "get_connection", _connection(cursor))

    delivery = billing.prepare_invoice_delivery(42, COMMUNITY, "admin-building")

    assert delivery["delivery_method"] == "email"
    inserts = [
        q for q, _ in cursor.executed if "INSERT INTO invoice_delivery_jobs" in q
    ]
    assert len(inserts) == 1
    first_query, first_params = cursor.executed[0]
    assert "community_id = %s" in first_query and "FOR UPDATE OF i" in first_query
    assert first_params == (42, COMMUNITY)


def test_admin_invoice_query_exposes_uncertain_delivery_status(monkeypatch):
    import database
    from store import billing

    cursor = _LifecycleCursor(rows=[[{"id": 42, "delivery_job_status": "failed"}]])
    monkeypatch.setattr(database, "get_connection", _connection(cursor))

    result = billing.list_community_invoices(COMMUNITY)

    assert result == [{"id": 42, "delivery_job_status": "failed"}]
    query, params = cursor.executed[0]
    assert "LEFT JOIN invoice_delivery_jobs delivery" in query
    assert "delivery.status AS delivery_job_status" in query
    assert params == (COMMUNITY,)


def test_store_sent_delivery_is_idempotent(monkeypatch):
    import database
    from store import billing

    cursor = _LifecycleCursor(ones=[_issued_invoice(), None, {"status": "sent"}])
    monkeypatch.setattr(database, "get_connection", _connection(cursor))

    result = billing.prepare_invoice_delivery(42, COMMUNITY, "admin-building")

    assert result["already_delivered"] is True
    assert not any("INSERT INTO invoice_delivery_jobs" in q for q, _ in cursor.executed)


@pytest.mark.parametrize("job_status", ["pending", "failed"])
def test_store_uncertain_delivery_requires_admin_confirmation(monkeypatch, job_status):
    import database
    from store import billing

    cursor = _LifecycleCursor(ones=[_issued_invoice(), None, {"status": job_status}])
    monkeypatch.setattr(database, "get_connection", _connection(cursor))

    result = billing.prepare_invoice_delivery(42, COMMUNITY, "admin-building")

    assert result["confirmation_required"] is True
    assert not any("UPDATE invoice_delivery_jobs" in q for q, _ in cursor.executed)


def test_store_completion_appends_actor_and_previous_new_state(monkeypatch):
    import database
    from store import billing

    cursor = _LifecycleCursor(
        ones=[_issued_invoice("download"), None, {"status": "pending"}]
    )
    monkeypatch.setattr(database, "get_connection", _connection(cursor))

    result = billing.complete_invoice_delivery(42, COMMUNITY, "admin-building")

    assert result["lifecycle_state"] == "delivered"
    event_params = next(
        params
        for query, params in cursor.executed
        if "INSERT INTO invoice_lifecycle_events" in query
    )
    assert event_params[:6] == (
        42,
        COMMUNITY,
        "admin-building",
        "delivered",
        "issued",
        "delivered",
    )


def test_store_admin_confirmation_completes_uncertain_delivery(monkeypatch):
    import database
    from store import billing

    cursor = _LifecycleCursor(
        ones=[_issued_invoice("email"), None, {"status": "failed"}]
    )
    monkeypatch.setattr(database, "get_connection", _connection(cursor))

    result = billing.confirm_invoice_delivery(42, COMMUNITY, "admin-building")

    assert result["lifecycle_state"] == "delivered"
    event_params = next(
        params
        for query, params in cursor.executed
        if "INSERT INTO invoice_lifecycle_events" in query
    )
    assert event_params[3:7] == (
        "delivery_confirmed",
        "issued",
        "delivered",
        "Zustellung durch Admin bestätigt",
    )


def test_admin_audit_events_are_loaded_in_one_community_scoped_query(monkeypatch):
    import database
    from store import billing

    cursor = _LifecycleCursor(rows=[[{"invoice_id": 42, "new_state": "delivered"}]])
    monkeypatch.setattr(database, "get_connection", _connection(cursor))

    result = billing.list_community_invoice_events(COMMUNITY)

    assert result == [{"invoice_id": 42, "new_state": "delivered"}]
    assert len(cursor.executed) == 1
    query, params = cursor.executed[0]
    assert "JOIN invoices i ON i.id = e.invoice_id" in query
    assert "e.community_id = %s AND i.community_id = %s" in query
    assert params == (COMMUNITY, COMMUNITY)


def test_store_payment_records_date_reference_and_audit(monkeypatch):
    import database
    from store import billing

    cursor = _LifecycleCursor(ones=[_issued_invoice(), {"new_state": "delivered"}])
    monkeypatch.setattr(database, "get_connection", _connection(cursor))

    billing.record_invoice_payment(
        42, COMMUNITY, "admin-building", date(2026, 9, 1), "BANK-42"
    )

    event_params = next(
        params
        for query, params in cursor.executed
        if "INSERT INTO invoice_lifecycle_events" in query
    )
    assert event_params[2:6] == (
        "admin-building",
        "paid",
        "delivered",
        "paid",
    )
    assert event_params[7:9] == ("BANK-42", date(2026, 9, 1))


def test_store_trims_payment_reference_and_cancellation_reason(monkeypatch):
    import database
    from store import billing

    payment_cursor = _LifecycleCursor(
        ones=[_issued_invoice(), {"new_state": "delivered"}]
    )
    monkeypatch.setattr(database, "get_connection", _connection(payment_cursor))
    billing.record_invoice_payment(
        42, COMMUNITY, "admin-building", date(2026, 9, 1), "  BANK-42  "
    )
    payment_event = next(
        params
        for query, params in payment_cursor.executed
        if "INSERT INTO invoice_lifecycle_events" in query
    )
    assert payment_event[7] == "BANK-42"

    cancel_cursor = _LifecycleCursor(ones=[_issued_invoice(), None])
    monkeypatch.setattr(database, "get_connection", _connection(cancel_cursor))
    billing.cancel_invoice(42, COMMUNITY, "admin-building", "  Falscher Tarif  ")
    cancel_event = next(
        params
        for query, params in cancel_cursor.executed
        if "INSERT INTO invoice_lifecycle_events" in query
    )
    assert cancel_event[3:7] == (
        "cancelled",
        "issued",
        "cancelled",
        "Falscher Tarif",
    )


def test_failed_delivery_attempt_is_append_only_audit(monkeypatch):
    import database
    from store import billing

    cursor = _LifecycleCursor(ones=[{"attempt_count": 2}, None])
    monkeypatch.setattr(database, "get_connection", _connection(cursor))

    billing.fail_invoice_delivery(
        42, COMMUNITY, "admin-building", "SMTP nicht erreichbar"
    )

    event_params = next(
        params
        for query, params in cursor.executed
        if "INSERT INTO invoice_lifecycle_events" in query
    )
    assert event_params[2:7] == (
        "admin-building",
        "delivery_failed",
        "issued",
        "issued",
        "SMTP nicht erreichbar",
    )
    assert event_params[9] == "delivery_failed:2"


def test_store_payment_retry_with_same_evidence_is_idempotent(monkeypatch):
    import database
    from store import billing

    cursor = _LifecycleCursor(
        ones=[
            _issued_invoice(),
            {"new_state": "paid"},
            {
                "reason": None,
                "reference": "BANK-42",
                "effective_date": date(2026, 9, 1),
            },
        ]
    )
    monkeypatch.setattr(database, "get_connection", _connection(cursor))

    result = billing.record_invoice_payment(
        42, COMMUNITY, "admin-building", date(2026, 9, 1), "BANK-42"
    )

    assert result == {"lifecycle_state": "paid", "already_recorded": True}
    assert not any(
        "INSERT INTO invoice_lifecycle_events" in query for query, _ in cursor.executed
    )


def test_store_correction_links_two_issued_rows_and_audits_both(monkeypatch):
    import database
    from store import billing

    cursor = _LifecycleCursor(
        rows=[
            [
                {
                    "id": 42,
                    "participant_id": "member-building",
                    "invoice_number": "LEG-2026-000001",
                },
                {
                    "id": 43,
                    "participant_id": "member-building",
                    "invoice_number": "LEG-2026-000002",
                },
            ]
        ],
        ones=[None, {"new_state": "cancelled"}, None],
    )
    monkeypatch.setattr(database, "get_connection", _connection(cursor))

    result = billing.correct_invoice(
        42, 43, COMMUNITY, "admin-building", "Neuer Abrechnungslauf"
    )

    assert result == {"lifecycle_state": "corrected", "already_corrected": False}
    assert any("INSERT INTO invoice_corrections" in q for q, _ in cursor.executed)
    events = [
        params
        for query, params in cursor.executed
        if "INSERT INTO invoice_lifecycle_events" in query
    ]
    assert len(events) == 2
    assert events[0][3:6] == ("corrected", "cancelled", "corrected")
    assert events[1][3:6] == ("correction_issued", "issued", "issued")


def test_store_correction_retry_returns_existing_link(monkeypatch):
    import database
    from store import billing

    cursor = _LifecycleCursor(
        rows=[
            [
                {
                    "id": 42,
                    "participant_id": "member-building",
                    "invoice_number": "LEG-2026-000001",
                },
                {
                    "id": 43,
                    "participant_id": "member-building",
                    "invoice_number": "LEG-2026-000002",
                },
            ]
        ],
        ones=[
            {
                "original_invoice_id": 42,
                "corrected_invoice_id": 43,
                "reason": "Neuer Abrechnungslauf",
            },
        ],
    )
    monkeypatch.setattr(database, "get_connection", _connection(cursor))

    result = billing.correct_invoice(
        42, 43, COMMUNITY, "admin-building", "Neuer Abrechnungslauf"
    )

    assert result == {"lifecycle_state": "corrected", "already_corrected": True}
    assert not any("INSERT INTO invoice_corrections" in q for q, _ in cursor.executed)


def test_lifecycle_store_seams_are_reexported_from_database():
    import database
    from store import billing

    for name in (
        "list_community_invoices",
        "prepare_invoice_delivery",
        "complete_invoice_delivery",
        "confirm_invoice_delivery",
        "fail_invoice_delivery",
        "record_invoice_payment",
        "cancel_invoice",
        "correct_invoice",
        "list_invoice_events",
        "list_community_invoice_events",
    ):
        assert getattr(database, name) is getattr(billing, name)
