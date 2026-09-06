# SPDX-License-Identifier: AGPL-3.0-or-later
"""Display-ready read model for a member's own issued invoices.

Every value comes from the frozen invoices row -- policy_snapshot,
provenance_snapshot, line_items_snapshot, net_chf, vat_rate_pct, vat_chf,
gross_chf -- so the private detail page and its PDF download always render
identical figures for one invoice_id. Nothing here recomputes a charge,
credit, or total from mutable billing tables, the current billing policy, or
live meter readings; the frozen invoice row is the only source of truth.

An issued invoice is supposed to be immutable and well-formed (billing_
approval.py validates it at issuance time), but this display layer does not
trust that blindly: a malformed, incomplete, or non-finite snapshot fails
closed with MemberInvoiceDataError rather than rendering an invented 0.00, an
empty items list, or "Unbekannt" as if that were real data.
"""

import json
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from markupsafe import escape

import billing_lifecycle
import billing_policy
import billing_workspace
import database as db
import document_generator

_ITEM_TYPE_LABELS = {
    "consumer_charge": "Verbrauchskosten",
    "producer_credit": "Produzentengutschrift",
    "rounding_adjustment": "Rundungsausgleich",
}
_ALLOWED_ITEM_TYPES = tuple(_ITEM_TYPE_LABELS)


class MemberInvoiceDataError(RuntimeError):
    """A stored invoice snapshot is malformed and cannot be safely displayed."""


def _require_json_dict(value, message: str) -> dict:
    """Decode a PostgreSQL JSONB value that may already be a dict, or a JSON
    string; fail closed on anything malformed, missing, or empty."""
    decoded = value
    if isinstance(decoded, str):
        try:
            decoded = json.loads(decoded)
        except (TypeError, ValueError):
            raise MemberInvoiceDataError(message) from None
    if not isinstance(decoded, dict) or not decoded:
        raise MemberInvoiceDataError(message)
    return decoded


def _require_json_list(value, message: str) -> list:
    """Decode a PostgreSQL JSONB value that may already be a list, or a JSON
    string; fail closed on anything malformed, missing, or empty."""
    decoded = value
    if isinstance(decoded, str):
        try:
            decoded = json.loads(decoded)
        except (TypeError, ValueError):
            raise MemberInvoiceDataError(message) from None
    if not isinstance(decoded, list) or not decoded:
        raise MemberInvoiceDataError(message)
    return decoded


def _require_finite_decimal(value, message: str) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        raise MemberInvoiceDataError(message) from None
    if not amount.is_finite():
        raise MemberInvoiceDataError(message)
    return amount


def _require_date_text(value, message: str) -> str:
    """Require a real calendar date and return it as an ISO date string."""
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value).isoformat()[:10]
        except ValueError:
            raise MemberInvoiceDataError(message) from None
    if isinstance(value, (datetime, date)):
        return value.isoformat()[:10]
    raise MemberInvoiceDataError(message)


def _require_text(value, message: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MemberInvoiceDataError(message)
    return value


def _require_positive_id(value, message: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise MemberInvoiceDataError(message)
    return value


def _decimal_text(value: Decimal, places: int, scale: Decimal = Decimal(1)) -> str:
    """Format an already-validated finite decimal with half-up rounding."""
    try:
        quantum = Decimal(1).scaleb(-places)
        return format((value * scale).quantize(quantum, rounding=ROUND_HALF_UP), "f")
    except ArithmeticError as exc:
        raise MemberInvoiceDataError("Ein Betrag der Rechnung ist ungültig.") from exc


def _line_item_view(
    item: dict, participant_id: str, policy_unit_price: Decimal
) -> tuple[dict, Decimal]:
    if not isinstance(item, dict):
        raise MemberInvoiceDataError("Eine Rechnungsposition ist fehlerhaft.")
    item_type = item.get("item_type")
    if item_type not in _ALLOWED_ITEM_TYPES:
        raise MemberInvoiceDataError("Eine Rechnungsposition hat einen ungültigen Typ.")
    amount = _require_finite_decimal(
        item.get("amount_chf"), "Eine Rechnungsposition hat keinen gültigen Betrag."
    )
    if item.get("participant_id") != participant_id:
        raise MemberInvoiceDataError(
            "Eine Rechnungsposition gehört nicht zu dieser Rechnung."
        )
    if item_type == "rounding_adjustment":
        if (
            item.get("quantity_kwh") is not None
            or item.get("unit_price_chf_per_kwh") is not None
        ):
            raise MemberInvoiceDataError(
                "Ein Rundungsausgleich darf weder Menge noch Preis enthalten."
            )
        display_quantity_kwh = None
        display_unit_price_rp = None
    else:
        quantity = _require_finite_decimal(
            item.get("quantity_kwh"),
            "Eine Rechnungsposition hat keine gültige Menge.",
        )
        unit_price = _require_finite_decimal(
            item.get("unit_price_chf_per_kwh"),
            "Eine Rechnungsposition hat keinen gültigen Preis.",
        )
        if quantity < 0 or unit_price < 0 or unit_price != policy_unit_price:
            raise MemberInvoiceDataError(
                "Eine Rechnungsposition stimmt nicht mit der Richtlinie überein."
            )
        try:
            expected = (quantity * unit_price).quantize(
                Decimal("0.000001"), rounding=ROUND_HALF_UP
            )
        except ArithmeticError:
            raise MemberInvoiceDataError(
                "Der Betrag einer Rechnungsposition ist nicht berechenbar."
            ) from None
        if item_type == "producer_credit":
            expected = -expected
        if amount != expected:
            raise MemberInvoiceDataError(
                "Der Betrag einer Rechnungsposition ist rechnerisch inkonsistent."
            )
        display_quantity_kwh = _decimal_text(quantity, 3)
        display_unit_price_rp = _decimal_text(unit_price, 2, Decimal(100))
    return (
        {
            "item_type": item_type,
            "item_type_label": _ITEM_TYPE_LABELS[item_type],
            "display_quantity_kwh": display_quantity_kwh,
            "display_unit_price_rp": display_unit_price_rp,
            "display_amount_chf": _decimal_text(amount, 2),
        },
        amount,
    )


def _require_line_item_cardinality_and_rounding(
    rendered_items: list[tuple[dict, Decimal]],
    provenance: dict,
    participant_id: str,
) -> None:
    """Fail closed unless the rendered line items are cardinality-consistent
    and any rounding adjustment is a single residual that is either exactly
    proven by the frozen period proof or, for legacy snapshots, cent-bounded
    and carried by the period's selected producer."""
    line_items = [item for item, _ in rendered_items]
    non_rounding_types = [
        item["item_type"]
        for item in line_items
        if item["item_type"] != "rounding_adjustment"
    ]
    if len(non_rounding_types) != len(set(non_rounding_types)):
        raise MemberInvoiceDataError("Die Rechnung enthält doppelte Positionen.")
    rounding_items = [
        pair for pair in rendered_items if pair[0]["item_type"] == "rounding_adjustment"
    ]
    producer_credits = [
        pair for pair in rendered_items if pair[0]["item_type"] == "producer_credit"
    ]
    if len(rounding_items) > 1 or (rounding_items and not producer_credits):
        raise MemberInvoiceDataError("Der Rundungsausgleich ist nicht zulässig.")
    has_rounding_proof = "rounding_adjustment" in provenance
    frozen_rounding = provenance.get("rounding_adjustment")
    if rounding_items:
        rounding_amount = rounding_items[0][1]
        if has_rounding_proof:
            if not isinstance(frozen_rounding, dict):
                raise MemberInvoiceDataError(
                    "Der Rundungsausgleich ist nicht zulässig."
                )
            frozen_amount = _require_finite_decimal(
                frozen_rounding.get("amount_chf"),
                "Der Rundungsausgleich ist nicht zulässig.",
            )
            if (
                frozen_rounding.get("participant_id") != participant_id
                or frozen_amount != rounding_amount
            ):
                raise MemberInvoiceDataError(
                    "Der Rundungsausgleich ist nicht zulässig."
                )
        elif rounding_amount.copy_abs() > Decimal("0.01"):
            # Legacy snapshots predate the exact frozen rounding proof. They
            # may still render only when the residual is cent-bounded.
            raise MemberInvoiceDataError(
                "Der Rundungsausgleich übersteigt den zulässigen Restbetrag."
            )
        reconciliation = provenance.get("reconciliation")
        production = (
            reconciliation.get("production_per_participant")
            if isinstance(reconciliation, dict)
            else None
        )
        if not isinstance(production, dict) or not production:
            raise MemberInvoiceDataError("Der Rundungsausgleich ist nicht zulässig.")
        producer_ids = [key for key in production if isinstance(key, str) and key]
        if not producer_ids or participant_id != min(producer_ids):
            raise MemberInvoiceDataError("Der Rundungsausgleich ist nicht zulässig.")
    elif has_rounding_proof and frozen_rounding is not None:
        raise MemberInvoiceDataError("Der Rundungsausgleich ist nicht zulässig.")


def _summary(invoice: dict) -> dict:
    provenance = _require_json_dict(
        invoice.get("provenance_snapshot"),
        "Die Rechnung hat keine gültige Periodenangabe.",
    )
    period_start = _require_date_text(
        provenance.get("period_start"),
        "Die Rechnung hat keine gültige Periodenangabe.",
    )
    invoice_id = _require_positive_id(
        invoice.get("id"), "Die Rechnung hat keine gültige ID."
    )
    invoice_number = _require_text(
        invoice.get("invoice_number"),
        "Die Rechnung hat keine gültige Rechnungsnummer.",
    )
    issue_date = _require_date_text(
        invoice.get("issue_date"), "Die Rechnung hat kein gültiges Rechnungsdatum."
    )
    due_date = _require_date_text(
        invoice.get("due_date"), "Die Rechnung hat kein gültiges Fälligkeitsdatum."
    )
    gross = _require_finite_decimal(
        invoice.get("gross_chf"), "Die Rechnung hat keinen gültigen Gesamtbetrag."
    )
    try:
        lifecycle = billing_lifecycle.describe_invoice(invoice)
    except billing_lifecycle.InvoiceLifecycleError as exc:
        raise MemberInvoiceDataError(str(exc)) from exc
    return {
        "id": invoice_id,
        "invoice_number": invoice_number,
        "period_label": billing_workspace.period_label(period_start),
        "issue_date": issue_date,
        "due_date": due_date,
        "display_gross_chf": _decimal_text(gross, 2),
        "lifecycle_state": lifecycle["lifecycle_state"],
        "status_label": lifecycle["status_label"],
        "corrects_invoice_number": invoice.get("corrects_invoice_number"),
        "corrected_by_invoice_number": invoice.get("corrected_by_invoice_number"),
    }


def list_view(building_id: str) -> dict:
    """Own issued invoices only, newest first.

    Any single corrupted row fails the whole call closed (MemberInvoiceData-
    Error) rather than silently omitting it or showing invented values next
    to genuine ones.
    """
    invoices = db.get_invoices_for_participant(building_id)
    summaries = []
    for invoice in invoices:
        detail = _detail_from_invoice(invoice, building_id)
        summaries.append(
            {
                key: detail[key]
                for key in (
                    "id",
                    "invoice_number",
                    "period_label",
                    "issue_date",
                    "due_date",
                    "display_gross_chf",
                    "lifecycle_state",
                    "status_label",
                    "corrects_invoice_number",
                    "corrected_by_invoice_number",
                )
            }
        )
    return {"invoices": summaries}


def _policy_snapshot_view(invoice: dict) -> tuple:
    """Validate the frozen policy_snapshot and return exactly the five values
    the detail view needs: vat_mode, policy_vat_rate, policy_unit_price,
    payment_days, policy_grid_fee. The grid fee stays None for legacy
    snapshots frozen before it became a persisted policy field."""
    policy = _require_json_dict(
        invoice.get("policy_snapshot"),
        "Die Rechnung hat keine gültige Richtlinien-Kopie.",
    )
    vat_mode = policy.get("vat_mode")
    if vat_mode not in billing_policy.VAT_MODES:
        raise MemberInvoiceDataError(
            "Die Rechnung hat einen ungültigen Mehrwertsteuer-Modus."
        )
    policy_vat_rate = _require_finite_decimal(
        policy.get("vat_rate_pct"),
        "Die Richtlinien-Kopie hat keinen gültigen Mehrwertsteuersatz.",
    )
    policy_unit_price = _require_finite_decimal(
        policy.get("internal_price_chf_per_kwh"),
        "Die Richtlinien-Kopie hat keinen gültigen Tarifpreis.",
    )
    if policy_unit_price < 0:
        raise MemberInvoiceDataError(
            "Die Richtlinien-Kopie hat einen ungültigen Tarifpreis."
        )
    frozen_grid_fee = policy.get("grid_fee_chf_per_kwh")
    if frozen_grid_fee is None:
        policy_grid_fee = None
    else:
        policy_grid_fee = _require_finite_decimal(
            frozen_grid_fee,
            "Die Richtlinien-Kopie hat ein ungültiges Netzentgelt.",
        )
        if policy_grid_fee < 0:
            raise MemberInvoiceDataError(
                "Die Richtlinien-Kopie hat ein ungültiges Netzentgelt."
            )
    payment_days = policy.get("payment_days")
    if (
        isinstance(payment_days, bool)
        or not isinstance(payment_days, int)
        or not billing_policy.MIN_PAYMENT_DAYS
        <= payment_days
        <= billing_policy.MAX_PAYMENT_DAYS
    ):
        raise MemberInvoiceDataError(
            "Die Richtlinien-Kopie hat keine gültige Zahlungsfrist."
        )
    return (
        vat_mode,
        policy_vat_rate,
        policy_unit_price,
        payment_days,
        policy_grid_fee,
    )


def _issuer_name(invoice: dict, provenance: dict) -> str:
    """Resolve the frozen issuer display name: the provenance issuer snapshot
    when present, otherwise the invoice's community_id for legacy snapshots
    issued before issuer-name snapshots existed."""
    community_id = _require_text(
        invoice.get("community_id"), "Die Rechnung hat keinen gültigen Aussteller."
    )
    issuer = provenance.get("issuer")
    if issuer is None:
        return community_id
    if not isinstance(issuer, dict) or issuer.get("community_id") != community_id:
        raise MemberInvoiceDataError("Die Rechnung hat keinen gültigen Aussteller.")
    return _require_text(
        issuer.get("name"), "Die Rechnung hat keinen gültigen Aussteller."
    )


def _require_invoice_totals(
    invoice: dict,
    vat_mode: str,
    policy_vat_rate: Decimal,
    rendered_items: list[tuple[dict, Decimal]],
) -> tuple[Decimal, Decimal, Decimal]:
    """Parse the frozen VAT/net/vat/gross figures and validate them against
    the policy snapshot and the rendered line items; return them in order."""
    vat_rate = _require_finite_decimal(
        invoice.get("vat_rate_pct"),
        "Die Rechnung hat keinen gültigen Mehrwertsteuersatz.",
    )
    net = _require_finite_decimal(
        invoice.get("net_chf"), "Die Rechnung hat keinen gültigen Nettobetrag."
    )
    vat = _require_finite_decimal(
        invoice.get("vat_chf"),
        "Die Rechnung hat keinen gültigen Mehrwertsteuerbetrag.",
    )
    gross = _require_finite_decimal(
        invoice.get("gross_chf"), "Die Rechnung hat keinen gültigen Gesamtbetrag."
    )
    if vat_rate != policy_vat_rate:
        raise MemberInvoiceDataError(
            "Die Mehrwertsteuer stimmt nicht mit der Richtlinien-Kopie überein."
        )
    if (vat_mode == "none" and vat_rate != 0) or (
        vat_mode == "standard"
        and (vat_rate <= 0 or vat_rate > billing_policy.MAX_VAT_RATE_PCT)
    ):
        raise MemberInvoiceDataError(
            "Die Rechnung hat einen ungültigen Mehrwertsteuersatz."
        )
    try:
        line_total = sum((amount for _, amount in rendered_items), Decimal(0)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    except ArithmeticError:
        raise MemberInvoiceDataError(
            "Die Rechnungssumme ist nicht berechenbar."
        ) from None
    if line_total != net:
        raise MemberInvoiceDataError(
            "Die Rechnungssumme stimmt nicht mit den Positionen überein."
        )
    try:
        expected_vat = (net * vat_rate / Decimal(100)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        expected_gross = net + vat
    except ArithmeticError:
        raise MemberInvoiceDataError(
            "Die Rechnungssummen sind nicht berechenbar."
        ) from None
    if vat != expected_vat or gross != expected_gross:
        raise MemberInvoiceDataError(
            "Die Rechnungssummen sind rechnerisch inkonsistent."
        )
    return vat_rate, net, vat


def _detail_from_invoice(invoice: dict, building_id: str) -> dict:
    """Validate and render one already owner-scoped immutable invoice row."""
    participant_id = _require_text(
        invoice.get("participant_id"),
        "Die Rechnung hat keine gültige Teilnehmer-ID.",
    )
    if participant_id != building_id:
        raise MemberInvoiceDataError("Die Rechnung hat eine ungültige Zuordnung.")

    vat_mode, policy_vat_rate, policy_unit_price, payment_days, policy_grid_fee = (
        _policy_snapshot_view(invoice)
    )

    provenance = _require_json_dict(
        invoice.get("provenance_snapshot"),
        "Die Rechnung hat keine gültige Periodenangabe.",
    )
    period_start = _require_date_text(
        provenance.get("period_start"),
        "Die Rechnung hat keine gültige Periodenangabe.",
    )
    period_end = _require_date_text(
        provenance.get("period_end"), "Die Rechnung hat keine gültige Periodenangabe."
    )

    line_items_raw = _require_json_list(
        invoice.get("line_items_snapshot"),
        "Die Rechnung hat keine gültigen Positionen.",
    )
    rendered_items = [
        _line_item_view(item, participant_id, policy_unit_price)
        for item in line_items_raw
    ]
    line_items = [item for item, _ in rendered_items]
    _require_line_item_cardinality_and_rounding(
        rendered_items, provenance, participant_id
    )

    issuer_name = _issuer_name(invoice, provenance)

    vat_rate, net, vat = _require_invoice_totals(
        invoice, vat_mode, policy_vat_rate, rendered_items
    )

    summary = _summary(invoice)
    period_start_date = date.fromisoformat(period_start)
    period_end_date = date.fromisoformat(period_end)
    issue_date = date.fromisoformat(summary["issue_date"])
    due_date = date.fromisoformat(summary["due_date"])
    if not (
        period_start_date < period_end_date <= issue_date
        and due_date == issue_date + timedelta(days=payment_days)
    ):
        raise MemberInvoiceDataError(
            "Die Datumsangaben der Rechnung sind inkonsistent."
        )

    return {
        **summary,
        "issuer_name": issuer_name,
        "period_start": period_start,
        "period_end": period_end,
        "vat_mode_label": billing_policy.VAT_MODE_LABELS[vat_mode],
        "display_vat_rate_pct": _decimal_text(vat_rate, 2),
        "display_policy_unit_price_rp": _decimal_text(
            policy_unit_price, 2, Decimal(100)
        ),
        "display_grid_fee_rp": (
            _decimal_text(policy_grid_fee, 2, Decimal(100))
            if policy_grid_fee is not None
            else None
        ),
        "policy_payment_days": payment_days,
        "display_net_chf": _decimal_text(net, 2),
        "display_vat_chf": _decimal_text(vat, 2),
        "charges": [i for i in line_items if i["item_type"] == "consumer_charge"],
        "credits": [i for i in line_items if i["item_type"] == "producer_credit"],
        "rounding_adjustments": [
            i for i in line_items if i["item_type"] == "rounding_adjustment"
        ],
    }


def detail_view(invoice_id: int, building_id: str) -> dict | None:
    """One own issued invoice, or None for a missing or another member's id.

    A wrong id and a nonexistent id are indistinguishable at the store seam:
    both come back as None here. A malformed owned row fails closed.
    """
    invoice = db.get_invoice_for_participant(invoice_id, building_id)
    if not invoice:
        return None
    return _detail_from_invoice(invoice, building_id)


def _rows_html(items) -> str:
    rows = []
    for item in items:
        rows.append(
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                escape(item.get("item_type_label", "")),
                escape(item.get("display_quantity_kwh") or "-"),
                escape(item.get("display_unit_price_rp") or "-"),
                escape(item.get("display_amount_chf", "0.00")),
            )
        )
    return "".join(rows)


def render_pdf(invoice: dict) -> bytes:
    """Render the exact detail_view() dict as a printable PDF.

    Every interpolated value is markupsafe-escaped before it reaches the
    existing WeasyPrint seam (document_generator.render_pdf_html). Nothing here
    recomputes a charge, credit, or total: every figure is already the
    frozen, display-ready value detail_view() produced, so the PDF can never
    diverge from what the member already saw on the HTML page.
    """
    charges_rows = _rows_html(invoice.get("charges", []))
    credit_items = list(invoice.get("credits", [])) + list(
        invoice.get("rounding_adjustments", [])
    )
    credits_rows = _rows_html(credit_items)

    invoice_number = escape(invoice.get("invoice_number", ""))
    issuer_name = escape(invoice.get("issuer_name", ""))
    period_label = escape(invoice.get("period_label", ""))
    issue_date = escape(invoice.get("issue_date", ""))
    due_date = escape(invoice.get("due_date", ""))
    vat_mode_label = escape(invoice.get("vat_mode_label", ""))
    vat_rate = escape(invoice.get("display_vat_rate_pct", "0.00"))
    policy_unit_price = escape(invoice.get("display_policy_unit_price_rp", ""))
    grid_fee_rp = invoice.get("display_grid_fee_rp")
    payment_days = escape(str(invoice.get("policy_payment_days", "")))
    net_chf = escape(invoice.get("display_net_chf", "0.00"))
    vat_chf = escape(invoice.get("display_vat_chf", "0.00"))
    gross_chf = escape(invoice.get("display_gross_chf", "0.00"))
    grid_fee_line = (
        f"<strong>Netzentgelt:</strong> {escape(grid_fee_rp)} Rp./kWh<br>"
        if grid_fee_rp
        else ""
    )

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
body {{ font-family: Arial, sans-serif; font-size: 11pt; margin: 2cm; }}
h1 {{ font-size: 16pt; }}
h2 {{ font-size: 13pt; margin-top: 1.5em; }}
table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
td, th {{ border: 1px solid #333; padding: 6px 8px; text-align: left; font-size: 10pt; }}
th {{ background: #f0f0f0; }}
.footer {{ margin-top: 3em; font-size: 9pt; color: #666; }}
</style></head><body>
<h1>Rechnung {invoice_number}</h1>
<p><strong>Aussteller:</strong> {issuer_name}<br>
<strong>Periode:</strong> {period_label}<br>
<strong>Rechnungsdatum:</strong> {issue_date}<br>
<strong>Fällig am:</strong> {due_date}<br>
<strong>Interner Preis:</strong> {policy_unit_price} Rp./kWh<br>
{grid_fee_line}<strong>Zahlungsfrist:</strong> {payment_days} Tage</p>

<h2>Verbrauchskosten</h2>
<table><tr><th>Position</th><th>Menge (kWh)</th><th>Preis (Rp./kWh)</th><th>Betrag (CHF)</th></tr>
{charges_rows or '<tr><td colspan="4">Keine Verbrauchskosten</td></tr>'}
</table>

<h2>Gutschriften und Ausgleich</h2>
<table><tr><th>Position</th><th>Menge (kWh)</th><th>Preis (Rp./kWh)</th><th>Betrag (CHF)</th></tr>
{credits_rows or '<tr><td colspan="4">Keine Gutschriften</td></tr>'}
</table>

<h2>Mehrwertsteuer</h2>
<p>{vat_mode_label} ({vat_rate}%)</p>

<h2>Total</h2>
<table>
<tr><td>Nettobetrag</td><td>{net_chf} CHF</td></tr>
<tr><td>Mehrwertsteuer</td><td>{vat_chf} CHF</td></tr>
<tr><td>Gesamtbetrag</td><td>{gross_chf} CHF</td></tr>
</table>

<div class="footer">Erstellt mit OpenLEG</div>
</body></html>"""

    return document_generator.render_pdf_html(html)
