# SPDX-License-Identifier: AGPL-3.0-or-later
"""Local statement fixtures and review controls over ordinary payment commands."""

import io
import os
from decimal import Decimal

from flask import abort, redirect, render_template, request, send_file
from psycopg2 import Error as DatabaseError

import community_access
import database as db
import payment_reconciliation
from dashboard_routes import (
    _dashboard_csrf_token,
    _require_dashboard_csrf,
    _require_dashboard_session,
)
from sandbox_metering_fixtures import COMMUNITY
from sandbox_payment_fixtures import KINDS, fixture
from translation_markers import gettext_noop as _N

LABELS = {
    "exact": _N("Exakte CHF-Gutschrift"),
    "unknown-reference": _N("Unbekannte Referenz, passender Betrag"),
    "wrong-amount": _N("Falscher Betrag"),
    "wrong-currency": _N("Falsche Währung"),
    "debit": _N("Belastung statt Gutschrift"),
    "reversal": _N("Rückbuchung"),
}


def _authorize(*, write=False):
    actor = _require_dashboard_session()
    if write:
        _require_dashboard_csrf()
    community = db.fetch_community_with_members(COMMUNITY, actor)
    member = next(
        (m for m in (community or {}).get("members", []) if m["building_id"] == actor),
        None,
    )
    if (
        not member
        or member.get("status") != "confirmed"
        or not community_access.is_administrator(member)
    ):
        abort(403)
    return actor


def _positive_invoices():
    return [
        row
        for row in db.list_community_invoices(COMMUNITY)
        if row.get("gross_chf") is not None
        and Decimal(str(row["gross_chf"])).is_finite()
        and Decimal(str(row["gross_chf"])) > 0
    ]


def _present(error=None, status=200):
    try:
        invoices = _positive_invoices()
        selected = request.args.get("invoice", type=int)
        if "invoice" in request.args and not any(
            row["id"] == selected for row in invoices
        ):
            abort(404)
        invoice = next(
            (row for row in invoices if row["id"] == selected),
            invoices[0] if invoices else None,
        )
        entries = db.list_bank_statement_entries(COMMUNITY)
        events = [
            row
            for row in db.list_community_operator_events(COMMUNITY)
            if row["event_type"] in {"invoice.paid", "payment.match.confirmed"}
        ]
        audit = db.list_community_invoice_events(COMMUNITY)
    except (db.BillingStoreError, DatabaseError):
        abort(503)
    return render_template(
        "sandbox/payments.html",
        csrf_token=_dashboard_csrf_token(),
        community_id=COMMUNITY,
        invoices=invoices,
        invoice=invoice,
        entries=entries,
        events=events,
        audit=audit,
        labels=LABELS,
        decisions=payment_reconciliation.DECISION_LABELS,
        error=error,
    ), status


def install(web):
    """Expose no fictional statement controls in the normal application."""
    if os.environ.get("OPENLEG_SANDBOX_WORKER") != "1":
        raise RuntimeError("payment simulation requires a village worker")

    @web.get("/sandbox/payments")
    def sandbox_payments():
        _authorize()
        return _present()

    @web.get("/sandbox/payments/fixtures/<int:invoice_id>/<kind>/<message>.xml")
    def sandbox_payment_fixture(invoice_id, kind, message):
        _authorize()
        if kind not in KINDS or message not in {"053", "054"}:
            abort(404)
        invoice = next(
            (row for row in _positive_invoices() if row["id"] == invoice_id), None
        )
        if not invoice:
            abort(404)
        return send_file(
            io.BytesIO(fixture(invoice, kind, message)),
            mimetype="application/xml",
            as_attachment=True,
            download_name=f"simulation-sonnwil-{invoice_id}-{kind}-camt{message}.xml",
        )

    @web.post("/sandbox/payments/confirm/<int:entry_id>")
    def sandbox_payment_confirm(entry_id):
        actor = _authorize(write=True)
        invoice_id = request.form.get("invoice_id", type=int)
        if request.form.get("confirm") != "yes" or not invoice_id or invoice_id < 1:
            abort(400)
        try:
            result = db.confirm_operator_payment_match(
                entry_id, invoice_id, COMMUNITY, actor, f"simulation-payment-{entry_id}"
            )
        except (TypeError, ValueError):
            return _present(
                _N(
                    "Diese Auswahl ist nicht zulässig. Nur eine passende CHF-Gutschrift für eine zugestellte Rechnung kann verbucht werden."
                ),
                409,
            )
        except (db.BillingStoreError, DatabaseError):
            abort(503)
        if not result:
            return _present(
                _N(
                    "Dieser Eintrag fehlt oder kann nicht bestätigt werden. Prüfen Sie Rechnung, Status und Auszug."
                ),
                409,
            )
        return redirect("/sandbox/payments", code=303)
