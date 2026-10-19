# SPDX-License-Identifier: AGPL-3.0-or-later
"""Behavioral contract for #400: member invoice view and PDF download.

A member's private list must show only their own issued invoices; the detail
view must build its charges/credits/total display strictly from the frozen
invoice snapshot, including the issuing LEG identity; a missing
invoice_id and another participant's invoice_id must be indistinguishable;
both the HTML detail and the PDF download must be no-store and must render
identical figures, because the PDF is rendered from the exact same
detail_view() dict the HTML page used, not from a second calculation.
"""

import copy
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.test_dashboard_access_routes import _set_session
from tests.test_dashboard_access_routes import (  # noqa: F401
    app_module as dashboard_app_module,
)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

INVOICE_ROW = {
    "id": 42,
    "community_id": "community-a",
    "participant_id": "building-session",
    "invoice_number": "LEG-2026-000001",
    "policy_snapshot": {
        "vat_mode": "standard",
        "vat_rate_pct": "7.700",
        "internal_price_chf_per_kwh": "0.150000",
        "grid_fee_chf_per_kwh": "0.080000",
        "payment_days": 30,
    },
    "provenance_snapshot": {
        "period_start": "2026-07-01T00:00:00+02:00",
        "period_end": "2026-08-01T00:00:00+02:00",
        "issuer": {"community_id": "community-a", "name": "LEG Musterweg"},
        "rounding_adjustment": None,
    },
    "line_items_snapshot": [
        {
            "participant_id": "building-session",
            "item_type": "consumer_charge",
            "quantity_kwh": "120.500000",
            "unit_price_chf_per_kwh": "0.150000",
            "amount_chf": "18.075000",
        },
        {
            "participant_id": "building-session",
            "item_type": "producer_credit",
            "quantity_kwh": "40.000000",
            "unit_price_chf_per_kwh": "0.150000",
            "amount_chf": "-6.00",
        },
    ],
    "net_chf": "12.08",
    "vat_rate_pct": "7.700",
    "vat_chf": "0.93",
    "gross_chf": "13.01",
    "issue_date": "2026-08-05",
    "due_date": "2026-09-04",
    "status": "issued",
}

DETAIL_VIEW = {
    "id": 42,
    "invoice_number": "LEG-2026-000001",
    "issuer_name": "LEG Musterweg",
    "period_label": "Juli 2026",
    "issue_date": "2026-08-05",
    "due_date": "2026-09-04",
    "vat_mode_label": "Mehrwertsteuer ausweisen",
    "display_vat_rate_pct": "7.70",
    "display_policy_unit_price_rp": "15.00",
    "display_grid_fee_rp": "8.00",
    "policy_payment_days": 30,
    "display_net_chf": "12.08",
    "display_vat_chf": "0.93",
    "display_gross_chf": "13.01",
    "charges": [
        {
            "item_type": "consumer_charge",
            "item_type_label": "Verbrauchskosten",
            "display_quantity_kwh": "120.500",
            "display_unit_price_rp": "15.00",
            "display_amount_chf": "18.08",
        }
    ],
    "credits": [
        {
            "item_type": "producer_credit",
            "item_type_label": "Produzentengutschrift",
            "display_quantity_kwh": "40.000",
            "display_unit_price_rp": "15.00",
            "display_amount_chf": "-6.00",
        }
    ],
    "rounding_adjustments": [],
}


def _read_template(name: str) -> str:
    return (TEMPLATES_DIR / name).read_text(encoding="utf-8")


# === Pure view-model tests (member_invoices.py) ===


def test_member_invoice_list_view_only_includes_own_issued_invoices(monkeypatch):
    import member_invoices

    fetch = MagicMock(return_value=[INVOICE_ROW])
    monkeypatch.setattr(member_invoices.db, "get_invoices_for_participant", fetch)

    result = member_invoices.list_view("building-session")

    fetch.assert_called_once_with("building-session")
    assert result["invoices"][0]["invoice_number"] == "LEG-2026-000001"


def test_member_invoice_detail_view_builds_model_from_frozen_snapshot_only(
    monkeypatch,
):
    """The view model uses the frozen issuer snapshot and never performs a
    second lookup against the mutable communities table."""
    import member_invoices

    lookup = MagicMock(return_value=dict(INVOICE_ROW))
    monkeypatch.setattr(member_invoices.db, "get_invoice_for_participant", lookup)

    view = member_invoices.detail_view(42, "building-session")

    lookup.assert_called_once_with(42, "building-session")
    assert view["invoice_number"] == "LEG-2026-000001"
    assert view["issuer_name"] == "LEG Musterweg"
    assert view["vat_mode_label"]
    assert view["display_net_chf"] == "12.08"
    assert view["display_vat_chf"] == "0.93"
    assert view["display_gross_chf"] == "13.01"
    assert len(view["charges"]) == 1
    assert view["charges"][0]["item_type"] == "consumer_charge"
    assert len(view["credits"]) == 1
    assert view["credits"][0]["item_type"] == "producer_credit"


def test_member_invoice_detail_uses_the_frozen_battery_source(monkeypatch):
    import copy

    import member_invoices

    row = copy.deepcopy(INVOICE_ROW)
    row["provenance_snapshot"]["battery_source"] = {
        "battery_kwh": "18.500000",
        "value_chf": "2.78",
    }
    monkeypatch.setattr(
        member_invoices.db,
        "get_invoice_for_participant",
        MagicMock(return_value=row),
    )

    view = member_invoices.detail_view(42, "building-session")

    assert view["battery_source"] == {
        "battery_kwh": member_invoices.Decimal("18.500000"),
        "value_chf": member_invoices.Decimal("2.78"),
    }
    assert view["display_battery_kwh"] == "18.500"
    assert view["display_battery_value_chf"] == "2.78"


def test_member_invoice_detail_hides_battery_source_without_a_frozen_allocation(
    monkeypatch,
):
    import member_invoices

    monkeypatch.setattr(
        member_invoices.db,
        "get_invoice_for_participant",
        MagicMock(return_value=INVOICE_ROW),
    )

    view = member_invoices.detail_view(42, "building-session")

    assert view["battery_source"] is None
    assert view["display_battery_kwh"] is None
    assert view["display_battery_value_chf"] is None


@pytest.mark.parametrize(
    "source",
    ["not a mapping", {}, {"battery_kwh": "-1", "value_chf": "1"}],
)
def test_member_invoice_detail_fails_closed_on_invalid_battery_source(
    monkeypatch, source
):
    import copy

    import member_invoices

    row = copy.deepcopy(INVOICE_ROW)
    row["provenance_snapshot"]["battery_source"] = source
    monkeypatch.setattr(
        member_invoices.db,
        "get_invoice_for_participant",
        MagicMock(return_value=row),
    )

    with pytest.raises(member_invoices.MemberInvoiceDataError, match="Quartierakku"):
        member_invoices.detail_view(42, "building-session")


def test_member_invoice_detail_uses_frozen_community_id_for_legacy_invoice(
    monkeypatch,
):
    """Invoices issued before issuer-name snapshots remain immutable: their
    frozen community id is shown instead of consulting the live community."""
    import copy

    import member_invoices

    row = copy.deepcopy(INVOICE_ROW)
    row["provenance_snapshot"].pop("issuer")
    monkeypatch.setattr(
        member_invoices.db,
        "get_invoice_for_participant",
        MagicMock(return_value=row),
    )

    assert member_invoices.detail_view(42, "building-session")["issuer_name"] == (
        "community-a"
    )


def test_member_invoice_detail_view_carries_the_frozen_policy_figures(monkeypatch):
    """The detail view restates what the frozen policy_snapshot priced: the
    internal tariff, the grid fee, and the payment term, so the invoice is
    traceable without consulting the policy page."""
    import member_invoices

    monkeypatch.setattr(
        member_invoices.db,
        "get_invoice_for_participant",
        MagicMock(return_value=dict(INVOICE_ROW)),
    )

    view = member_invoices.detail_view(42, "building-session")

    assert view["display_policy_unit_price_rp"] == "15.00"
    assert view["display_grid_fee_rp"] == "8.00"
    assert view["policy_payment_days"] == 30


def test_member_invoice_detail_view_tolerates_legacy_snapshot_without_grid_fee(
    monkeypatch,
):
    """Invoices frozen before the grid fee became a persisted policy field
    keep rendering: the Netzentgelt line is omitted, never invented."""
    import copy

    import member_invoices

    row = copy.deepcopy(INVOICE_ROW)
    del row["policy_snapshot"]["grid_fee_chf_per_kwh"]
    monkeypatch.setattr(
        member_invoices.db,
        "get_invoice_for_participant",
        MagicMock(return_value=row),
    )

    view = member_invoices.detail_view(42, "building-session")

    assert view["display_policy_unit_price_rp"] == "15.00"
    assert view["display_grid_fee_rp"] is None
    assert view["policy_payment_days"] == 30


def test_every_participant_snapshot_from_approval_is_readable():
    """A period-wide rounding adjustment belongs only to its selected
    participant; it must not invalidate the other issued invoices."""
    from datetime import date

    import billing_approval
    import member_invoices
    from tests.test_billing_approval import _draft

    snapshots = billing_approval.prepare_invoice_snapshots(
        _draft(), issue_date=date(2026, 2, 5)
    )

    for invoice_id, snapshot in enumerate(snapshots, start=1):
        provenance = dict(snapshot["provenance_snapshot"])
        provenance["issuer"] = {
            "community_id": "community-a",
            "name": "LEG Musterweg",
        }
        row = {
            **snapshot,
            "id": invoice_id,
            "community_id": "community-a",
            "invoice_number": f"MUSTER-2026-{invoice_id:06d}",
            "provenance_snapshot": provenance,
        }

        view = member_invoices._detail_from_invoice(row, snapshot["participant_id"])

        assert view["invoice_number"] == row["invoice_number"]


@pytest.mark.parametrize("lookup_result", [None])
def test_member_invoice_detail_view_returns_none_when_store_finds_nothing(
    monkeypatch, lookup_result
):
    """Missing id and another participant's id both fail the same store query,
    so the view model must not distinguish them either."""
    import member_invoices

    monkeypatch.setattr(
        member_invoices.db,
        "get_invoice_for_participant",
        MagicMock(return_value=lookup_result),
    )

    assert member_invoices.detail_view(999, "building-session") is None


# === PDF rendering tests: real HTML build, only the WeasyPrint seam mocked ===


def test_member_invoice_pdf_bytes_renders_swiss_german_html_from_the_detail_dict():
    """member_invoice_pdf_bytes must build real invoice HTML from exactly the
    dict the HTML page rendered -- not a second, independently computed model
    -- and hand it to the existing PDF renderer seam (document_generator's
    WeasyPrint wrapper), which is the only part mocked here."""
    import dashboard
    import document_generator

    with patch.object(
        document_generator, "_render_pdf", return_value=b"%PDF-fake"
    ) as render_pdf:
        pdf_bytes = dashboard.member_invoice_pdf_bytes(DETAIL_VIEW)

    assert pdf_bytes == b"%PDF-fake"
    render_pdf.assert_called_once()
    html = render_pdf.call_args.args[0]

    # Immutable snapshot values, identical to what the HTML page showed.
    assert "LEG-2026-000001" in html
    assert "LEG Musterweg" in html
    assert "Juli 2026" in html
    assert "2026-08-05" in html
    assert "2026-09-04" in html
    assert "Mehrwertsteuer ausweisen" in html
    assert "7.70" in html
    # The frozen policy summary restates the applied tariff on the PDF too.
    assert "Interner Preis" in html
    assert "15.00 Rp./kWh" in html
    assert "Netzentgelt" in html
    assert "8.00 Rp./kWh" in html
    assert "Zahlungsfrist" in html
    assert "30 Tage" in html
    assert "Verbrauchskosten" in html
    assert "18.08" in html
    assert "Produzentengutschrift" in html
    assert "-6.00" in html or "6.00" in html
    assert "12.08" in html
    assert "0.93" in html
    assert "13.01" in html
    assert "Erstellt mit OpenLEG" in html

    # Swiss High German: no ß or typographic dashes (docs/engineering-contract.md).
    assert "ß" not in html
    assert "–" not in html, "no en dash in generated invoice PDF HTML"
    assert "—" not in html, "no em dash in generated invoice PDF HTML"


def test_member_invoice_pdf_omits_grid_fee_for_legacy_snapshots():
    """A legacy snapshot without a frozen grid fee prints no Netzentgelt line
    instead of an invented placeholder price."""
    import dashboard
    import document_generator

    legacy_view = {**DETAIL_VIEW, "display_grid_fee_rp": None}

    with patch.object(
        document_generator, "_render_pdf", return_value=b"%PDF-fake"
    ) as render_pdf:
        dashboard.member_invoice_pdf_bytes(legacy_view)

    html = render_pdf.call_args.args[0]
    assert "Netzentgelt" not in html
    assert "Interner Preis" in html


def test_member_invoice_pdf_bytes_escapes_hostile_snapshot_strings():
    """Snapshot fields ultimately come from data an admin entered (community
    name, invoice prefix); the PDF builder must escape them like every other
    document_generator template does, or a hostile issuer name becomes markup
    in the rendered invoice HTML."""
    import dashboard
    import document_generator

    hostile_view = {
        **DETAIL_VIEW,
        "issuer_name": "<script>alert(1)</script>",
        "invoice_number": 'LEG" onmouseover="alert(1)',
        "charges": [
            {
                "item_type": "consumer_charge",
                "item_type_label": "<b>Verbrauch</b>",
                "display_quantity_kwh": "1.000",
                "display_unit_price_rp": "15.00",
                "display_amount_chf": "1.00",
            }
        ],
        "credits": [],
    }

    with patch.object(
        document_generator, "_render_pdf", return_value=b"%PDF-fake"
    ) as render_pdf:
        dashboard.member_invoice_pdf_bytes(hostile_view)

    html = render_pdf.call_args.args[0]
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "<b>Verbrauch</b>" not in html
    assert "&lt;b&gt;Verbrauch&lt;/b&gt;" in html
    assert 'onmouseover="alert(1)"' not in html


def test_member_invoice_pdf_bytes_uses_plain_hyphen_for_missing_line_item_fields():
    """A rounding adjustment carries neither quantity nor unit price. The
    placeholder for that empty cell must be a plain hyphen or explicit
    "Nicht angegeben", never a typographic en/em dash (engineering contract)."""
    import dashboard
    import document_generator

    view = {
        **DETAIL_VIEW,
        "charges": [],
        "credits": [],
        "rounding_adjustments": [
            {
                "item_type": "rounding_adjustment",
                "item_type_label": "Rundungsausgleich",
                "display_quantity_kwh": None,
                "display_unit_price_rp": None,
                "display_amount_chf": "-0.01",
            }
        ],
    }

    with patch.object(
        document_generator, "_render_pdf", return_value=b"%PDF-fake"
    ) as render_pdf:
        dashboard.member_invoice_pdf_bytes(view)

    html = render_pdf.call_args.args[0]
    assert "–" not in html
    assert "—" not in html
    assert "-" in html or "Nicht angegeben" in html


# === Fail-closed data integrity (member_invoices.MemberInvoiceDataError) ===
#
# An issued invoice's snapshot columns are supposed to be immutable and
# well-formed (billing_approval.py validates them at issuance time), but the
# display layer must not trust that blindly: a malformed row must never
# render an invented 0.00, an empty items list, or "Unbekannt" as if that
# were real data. It must fail closed instead.


def _corrupt_invoice_row(**overrides):
    import copy

    row = copy.deepcopy(INVOICE_ROW)
    row.update(overrides)
    return row


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"policy_snapshot": None}, "missing policy snapshot"),
        ({"policy_snapshot": "{not json"}, "malformed policy snapshot JSON"),
        ({"policy_snapshot": {}}, "empty policy snapshot"),
        (
            {"policy_snapshot": {"vat_mode": "bogus", "vat_rate_pct": "7.7"}},
            "invalid vat_mode",
        ),
        ({"provenance_snapshot": None}, "missing provenance snapshot"),
        (
            {"provenance_snapshot": {"period_start": "2026-07-01T00:00:00+02:00"}},
            "provenance missing period_end",
        ),
        ({"line_items_snapshot": None}, "missing line items"),
        ({"line_items_snapshot": []}, "empty line items"),
        ({"line_items_snapshot": "not a list"}, "line items not a list"),
        (
            {
                "line_items_snapshot": [
                    {"participant_id": "building-session", "item_type": "unknown"}
                ]
            },
            "invalid line item type",
        ),
        (
            {
                "line_items_snapshot": [
                    {
                        "participant_id": "building-session",
                        "item_type": "consumer_charge",
                        "quantity_kwh": "not-a-number",
                        "unit_price_chf_per_kwh": "0.15",
                        "amount_chf": "1.00",
                    }
                ]
            },
            "non-finite quantity",
        ),
        (
            {
                "line_items_snapshot": [
                    {
                        "participant_id": "building-session",
                        "item_type": "consumer_charge",
                        "quantity_kwh": "1.0",
                        "unit_price_chf_per_kwh": "0.15",
                        "amount_chf": "NaN",
                    }
                ]
            },
            "non-finite amount",
        ),
        ({"net_chf": "NaN"}, "non-finite net"),
        ({"vat_chf": None}, "missing vat amount"),
        ({"gross_chf": "not-a-number"}, "non-finite gross"),
        ({"vat_rate_pct": "infinity"}, "non-finite vat rate"),
        ({"issue_date": "not-a-date"}, "invalid issue date"),
        ({"issue_date": None}, "missing issue date"),
        ({"due_date": "not-a-date"}, "invalid due date"),
        ({"invoice_number": ""}, "empty invoice number"),
        ({"invoice_number": None}, "missing invoice number"),
        ({"participant_id": "building-other"}, "invoice owner mismatch"),
        ({"id": 0}, "invalid invoice id"),
        (
            {
                "policy_snapshot": {
                    "vat_mode": "standard",
                    "vat_rate_pct": "8.100",
                }
            },
            "policy and invoice VAT mismatch",
        ),
        ({"vat_chf": "123.45"}, "VAT arithmetic mismatch"),
        ({"gross_chf": "0.01"}, "gross arithmetic mismatch"),
        ({"net_chf": "999.00"}, "line sum mismatch"),
        (
            {
                "line_items_snapshot": [
                    {
                        "participant_id": "building-other",
                        "item_type": "consumer_charge",
                        "quantity_kwh": "120.500000",
                        "unit_price_chf_per_kwh": "0.150000",
                        "amount_chf": "18.075000",
                    }
                ]
            },
            "cross-member line item",
        ),
        (
            {
                "line_items_snapshot": [
                    {
                        "participant_id": "building-session",
                        "item_type": "consumer_charge",
                        "quantity_kwh": "120.500000",
                        "unit_price_chf_per_kwh": "0.150000",
                        "amount_chf": "99.000000",
                    }
                ]
            },
            "quantity-price arithmetic mismatch",
        ),
    ],
)
def test_detail_view_fails_closed_on_corrupted_snapshot(monkeypatch, overrides, reason):
    import member_invoices

    corrupted = _corrupt_invoice_row(**overrides)
    monkeypatch.setattr(
        member_invoices.db,
        "get_invoice_for_participant",
        MagicMock(return_value=corrupted),
    )

    with pytest.raises(member_invoices.MemberInvoiceDataError):
        member_invoices.detail_view(42, "building-session")


def test_detail_view_rejects_zero_vat_rate_in_standard_mode(monkeypatch):
    """A standard-mode invoice with a zero frozen rate is arithmetically
    coherent (vat 0.00, gross equal net) yet violates the standard-mode rule
    that the rate must be positive, so it must fail closed."""
    import member_invoices

    corrupted = _corrupt_invoice_row(
        policy_snapshot={
            **INVOICE_ROW["policy_snapshot"],
            "vat_rate_pct": "0",
        },
        vat_rate_pct="0",
        vat_chf="0.00",
        gross_chf="12.08",
    )
    monkeypatch.setattr(
        member_invoices.db,
        "get_invoice_for_participant",
        MagicMock(return_value=corrupted),
    )

    with pytest.raises(member_invoices.MemberInvoiceDataError) as excinfo:
        member_invoices.detail_view(42, "building-session")

    assert str(excinfo.value) == (
        "Die Rechnung hat einen ungültigen Mehrwertsteuersatz."
    )


def test_member_views_name_the_swiss_month_of_a_utc_period_start(monkeypatch):
    """The billing runner persists the period start as a UTC instant: August
    2026 begins at 2026-07-31T22:00Z. List, detail and PDF must still read
    "August 2026", not the UTC calendar date's month."""
    import member_invoices

    row = _corrupt_invoice_row(
        provenance_snapshot={
            **INVOICE_ROW["provenance_snapshot"],
            "period_start": "2026-07-31T22:00:00+00:00",
            "period_end": "2026-08-31T22:00:00+00:00",
        },
        issue_date="2026-09-05",
        due_date="2026-10-05",
    )
    monkeypatch.setattr(
        member_invoices.db,
        "get_invoices_for_participant",
        MagicMock(return_value=[row]),
    )
    monkeypatch.setattr(
        member_invoices.db,
        "get_invoice_for_participant",
        MagicMock(return_value=row),
    )

    listed = member_invoices.list_view("building-session")
    detail = member_invoices.detail_view(42, "building-session")

    assert [item["period_label"] for item in listed["invoices"]] == ["August 2026"]
    assert detail["period_label"] == "August 2026"


def test_list_view_fails_closed_on_corrupted_snapshot(monkeypatch):
    import member_invoices

    corrupted = _corrupt_invoice_row(gross_chf="NaN")
    monkeypatch.setattr(
        member_invoices.db,
        "get_invoices_for_participant",
        MagicMock(return_value=[corrupted]),
    )

    with pytest.raises(member_invoices.MemberInvoiceDataError):
        member_invoices.list_view("building-session")


def test_list_view_fails_closed_on_finite_but_inconsistent_total(monkeypatch):
    import member_invoices

    corrupted = _corrupt_invoice_row(gross_chf="999.99")
    monkeypatch.setattr(
        member_invoices.db,
        "get_invoices_for_participant",
        MagicMock(return_value=[corrupted]),
    )

    with pytest.raises(member_invoices.MemberInvoiceDataError):
        member_invoices.list_view("building-session")


@pytest.mark.parametrize(
    "line_items",
    [
        [
            {
                "participant_id": "building-session",
                "item_type": "rounding_adjustment",
                "quantity_kwh": None,
                "unit_price_chf_per_kwh": None,
                "amount_chf": "999.00",
            }
        ],
        [
            {
                "participant_id": "building-session",
                "item_type": "producer_credit",
                "quantity_kwh": "1.000000",
                "unit_price_chf_per_kwh": "0.150000",
                "amount_chf": "-0.150000",
            },
            {
                "participant_id": "building-session",
                "item_type": "rounding_adjustment",
                "quantity_kwh": None,
                "unit_price_chf_per_kwh": None,
                "amount_chf": "0.02",
            },
        ],
    ],
)
def test_detail_view_rejects_unbounded_or_unjustified_rounding(monkeypatch, line_items):
    import member_invoices

    corrupted = _corrupt_invoice_row(
        line_items_snapshot=line_items,
        net_chf="999.00",
        vat_chf="76.92",
        gross_chf="1075.92",
    )
    monkeypatch.setattr(
        member_invoices.db,
        "get_invoice_for_participant",
        MagicMock(return_value=corrupted),
    )

    with pytest.raises(member_invoices.MemberInvoiceDataError):
        member_invoices.detail_view(42, "building-session")


def test_detail_view_rejects_rounding_proof_without_rounding_item(monkeypatch):
    """A non-null frozen rounding_adjustment proof without a matching
    rounding_adjustment line item is an orphaned proof and must fail closed."""
    import member_invoices

    corrupted = _corrupt_invoice_row(
        provenance_snapshot={
            **INVOICE_ROW["provenance_snapshot"],
            "rounding_adjustment": {
                "participant_id": "building-session",
                "amount_chf": "0.01",
            },
        }
    )
    monkeypatch.setattr(
        member_invoices.db,
        "get_invoice_for_participant",
        MagicMock(return_value=corrupted),
    )

    with pytest.raises(member_invoices.MemberInvoiceDataError) as excinfo:
        member_invoices.detail_view(42, "building-session")

    assert str(excinfo.value) == "Der Rundungsausgleich ist nicht zulässig."


def test_detail_view_rejects_frozen_rounding_proof_participant_mismatch(monkeypatch):
    """The frozen rounding_adjustment proof must name the invoice's own
    participant; a proof reassigned to another participant must fail closed
    with the stable rounding error text, never render the adjustment."""
    import member_invoices

    row = _corrupt_invoice_row(
        line_items_snapshot=[
            {
                "participant_id": "building-session",
                "item_type": "producer_credit",
                "quantity_kwh": "40.000000",
                "unit_price_chf_per_kwh": "0.150000",
                "amount_chf": "-6.000000",
            },
            {
                "participant_id": "building-session",
                "item_type": "rounding_adjustment",
                "quantity_kwh": None,
                "unit_price_chf_per_kwh": None,
                "amount_chf": "0.01",
            },
        ],
        net_chf="-5.99",
        vat_chf="-0.46",
        gross_chf="-6.45",
        provenance_snapshot={
            **INVOICE_ROW["provenance_snapshot"],
            "rounding_adjustment": {
                "participant_id": "building-session",
                "amount_chf": "0.01",
            },
            "reconciliation": {
                "production_per_participant": {
                    "building-session": "40.000000",
                    "building-zeta": "10.000000",
                }
            },
        },
    )
    row["provenance_snapshot"]["rounding_adjustment"]["participant_id"] = (
        "building-other"
    )
    monkeypatch.setattr(
        member_invoices.db,
        "get_invoice_for_participant",
        MagicMock(return_value=row),
    )

    with pytest.raises(member_invoices.MemberInvoiceDataError) as excinfo:
        member_invoices.detail_view(42, "building-session")

    assert str(excinfo.value) == "Der Rundungsausgleich ist nicht zulässig."


def test_detail_view_rejects_issuer_snapshot_from_another_community(monkeypatch):
    """A frozen issuer whose community_id differs from the invoice's own
    community_id is an invalid issuer and must fail closed."""
    import member_invoices

    corrupted = _corrupt_invoice_row(
        provenance_snapshot={
            **INVOICE_ROW["provenance_snapshot"],
            "issuer": {"community_id": "community-b", "name": "Fremde LEG"},
        }
    )
    monkeypatch.setattr(
        member_invoices.db,
        "get_invoice_for_participant",
        MagicMock(return_value=corrupted),
    )

    with pytest.raises(member_invoices.MemberInvoiceDataError) as excinfo:
        member_invoices.detail_view(42, "building-session")

    assert str(excinfo.value) == "Die Rechnung hat keinen gültigen Aussteller."


def test_detail_view_rejects_duplicate_non_rounding_positions(monkeypatch):
    import copy

    import member_invoices

    duplicated = copy.deepcopy(INVOICE_ROW["line_items_snapshot"][0])
    corrupted = _corrupt_invoice_row(
        line_items_snapshot=[duplicated, copy.deepcopy(duplicated)],
        net_chf="36.15",
        vat_chf="2.78",
        gross_chf="38.93",
    )
    monkeypatch.setattr(
        member_invoices.db,
        "get_invoice_for_participant",
        MagicMock(return_value=corrupted),
    )

    with pytest.raises(member_invoices.MemberInvoiceDataError):
        member_invoices.detail_view(42, "building-session")


def test_detail_view_wraps_decimal_overflow_as_data_error(monkeypatch):
    import member_invoices

    corrupted = _corrupt_invoice_row()
    corrupted["line_items_snapshot"][0]["quantity_kwh"] = "1E+999999"
    monkeypatch.setattr(
        member_invoices.db,
        "get_invoice_for_participant",
        MagicMock(return_value=corrupted),
    )

    with pytest.raises(member_invoices.MemberInvoiceDataError):
        member_invoices.detail_view(42, "building-session")


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "provenance_snapshot": {
                **INVOICE_ROW["provenance_snapshot"],
                "period_start": "2026-08-01",
                "period_end": "2026-07-01",
            }
        },
        {"issue_date": "2026-07-15"},
        {"due_date": "2026-08-01"},
        {"due_date": "2027-12-31"},
        {
            "policy_snapshot": {
                **INVOICE_ROW["policy_snapshot"],
                "payment_days": 0,
            }
        },
    ],
)
def test_detail_view_rejects_impossible_date_order(monkeypatch, overrides):
    import member_invoices

    corrupted = _corrupt_invoice_row(**overrides)
    monkeypatch.setattr(
        member_invoices.db,
        "get_invoice_for_participant",
        MagicMock(return_value=corrupted),
    )

    with pytest.raises(member_invoices.MemberInvoiceDataError):
        member_invoices.detail_view(42, "building-session")


def test_detail_view_rejects_negative_frozen_tariff_price(monkeypatch):
    """A frozen policy_snapshot with a negative internal_price_chf_per_kwh
    must fail closed with the stable tariff-policy error text, never render
    the negative price as if it were a real tariff."""
    import member_invoices

    corrupted = _corrupt_invoice_row(
        policy_snapshot={
            **INVOICE_ROW["policy_snapshot"],
            "internal_price_chf_per_kwh": "-0.150000",
        }
    )
    monkeypatch.setattr(
        member_invoices.db,
        "get_invoice_for_participant",
        MagicMock(return_value=corrupted),
    )

    with pytest.raises(member_invoices.MemberInvoiceDataError) as excinfo:
        member_invoices.detail_view(42, "building-session")

    assert str(excinfo.value) == (
        "Die Richtlinien-Kopie hat einen ungültigen Tarifpreis."
    )


@pytest.mark.parametrize("grid_fee", ["NaN", "-0.080000", "not-a-number"])
def test_detail_view_fails_closed_on_corrupted_frozen_grid_fee(monkeypatch, grid_fee):
    """A frozen grid fee that is not a finite, non-negative number must fail
    closed with the stable grid-fee error text, never render as a tariff."""
    import member_invoices

    corrupted = _corrupt_invoice_row(
        policy_snapshot={
            **INVOICE_ROW["policy_snapshot"],
            "grid_fee_chf_per_kwh": grid_fee,
        }
    )
    monkeypatch.setattr(
        member_invoices.db,
        "get_invoice_for_participant",
        MagicMock(return_value=corrupted),
    )

    with pytest.raises(member_invoices.MemberInvoiceDataError) as excinfo:
        member_invoices.detail_view(42, "building-session")

    assert str(excinfo.value) == (
        "Die Richtlinien-Kopie hat ein ungültiges Netzentgelt."
    )


@pytest.mark.parametrize(
    "field,value", [("id", 0), ("provenance_snapshot", {"period_start": "not-a-date"})]
)
def test_list_view_validates_identity_and_period_date(monkeypatch, field, value):
    import member_invoices

    corrupted = _corrupt_invoice_row(**{field: value})
    monkeypatch.setattr(
        member_invoices.db,
        "get_invoices_for_participant",
        MagicMock(return_value=[corrupted]),
    )

    with pytest.raises(member_invoices.MemberInvoiceDataError):
        member_invoices.list_view("building-session")


def test_member_invoice_data_error_is_a_dedicated_exception_type():
    import member_invoices

    assert issubclass(member_invoices.MemberInvoiceDataError, Exception)
    assert (
        member_invoices.MemberInvoiceDataError
        is not member_invoices.db.BillingStoreError
    )


# === Route tests (dashboard_routes.py, full Flask app) ===


def _patch_invoice_detail(flask_app_module, monkeypatch, view_by_owner):
    def fake_detail(invoice_id, building_id):
        if building_id != "building-session":
            return None
        return view_by_owner.get(invoice_id)

    monkeypatch.setattr(
        flask_app_module.dashboard_module, "member_invoice_detail", fake_detail
    )


def _patch_invoice_savings(flask_app_module, monkeypatch):
    """The realized-savings section rides on the detail page; keep it out of
    the invoice snapshot contracts unless a test targets it."""
    monkeypatch.setattr(
        flask_app_module.dashboard_module,
        "member_invoice_savings_view",
        MagicMock(return_value={"available": False, "period_label": "", "message": ""}),
    )


def test_dashboard_invoices_list_requires_session(dashboard_app_module):  # noqa: F811
    client = dashboard_app_module.web.test_client()
    response = client.get("/dashboard/invoices")
    assert response.status_code == 401


def test_dashboard_invoices_list_is_private_and_session_scoped(
    dashboard_app_module,  # noqa: F811
    monkeypatch,
):
    fetch = MagicMock(return_value={"invoices": [{"id": 1, "invoice_number": "LEG-1"}]})
    monkeypatch.setattr(
        dashboard_app_module.dashboard_module, "member_invoices_view", fetch
    )
    client = dashboard_app_module.web.test_client()
    _set_session(client)

    response = client.get("/dashboard/invoices")

    assert response.status_code == 200
    fetch.assert_called_once_with("building-session")
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_dashboard_invoice_detail_requires_session(dashboard_app_module):  # noqa: F811
    client = dashboard_app_module.web.test_client()
    response = client.get("/dashboard/invoices/42")
    assert response.status_code == 401


def test_dashboard_invoice_detail_missing_and_cross_member_are_identical(
    dashboard_app_module,  # noqa: F811
    monkeypatch,
):
    _patch_invoice_detail(dashboard_app_module, monkeypatch, view_by_owner={})
    client = dashboard_app_module.web.test_client()
    _set_session(client)

    missing = client.get("/dashboard/invoices/999")
    other_members = client.get("/dashboard/invoices/42")

    assert missing.status_code == other_members.status_code == 404
    assert missing.get_data() == other_members.get_data()


def test_dashboard_invoice_detail_renders_own_invoice_privately(
    dashboard_app_module,  # noqa: F811
    monkeypatch,
):
    _patch_invoice_detail(
        dashboard_app_module, monkeypatch, view_by_owner={42: DETAIL_VIEW}
    )
    _patch_invoice_savings(dashboard_app_module, monkeypatch)
    client = dashboard_app_module.web.test_client()
    _set_session(client)

    response = client.get("/dashboard/invoices/42")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    body = response.get_data(as_text=True)
    assert "LEG-2026-000001" in body
    assert "LEG Musterweg" in body
    assert "13.01" in body


def test_dashboard_invoice_detail_renders_the_applied_policy_summary(
    dashboard_app_module,  # noqa: F811
    monkeypatch,
):
    """The detail page restates the frozen policy figures with the same
    labels the policy page uses, so the price is traceable on both."""
    _patch_invoice_detail(
        dashboard_app_module, monkeypatch, view_by_owner={42: DETAIL_VIEW}
    )
    _patch_invoice_savings(dashboard_app_module, monkeypatch)
    client = dashboard_app_module.web.test_client()
    _set_session(client)

    response = client.get("/dashboard/invoices/42")

    body = response.get_data(as_text=True)
    assert "Interner Preis" in body
    assert "15.00 Rp./kWh" in body
    assert "Netzentgelt" in body
    assert "8.00 Rp./kWh" in body
    assert "Zahlungsfrist" in body
    assert "30 Tage" in body


def test_dashboard_invoice_detail_renders_the_frozen_battery_source(
    dashboard_app_module,  # noqa: F811
    monkeypatch,
):
    view = {
        **DETAIL_VIEW,
        "battery_source": {"battery_kwh": "18.5", "value_chf": "2.78"},
        "display_battery_kwh": "18.500",
        "display_battery_value_chf": "2.78",
    }
    _patch_invoice_detail(dashboard_app_module, monkeypatch, view_by_owner={42: view})
    _patch_invoice_savings(dashboard_app_module, monkeypatch)
    client = dashboard_app_module.web.test_client()
    _set_session(client)

    body = client.get("/dashboard/invoices/42").get_data(as_text=True)

    assert "Strom aus dem Quartierakku" in body
    assert "18.500 kWh" in body
    assert "2.78 CHF" in body


def test_dashboard_invoice_detail_has_no_battery_placeholder_without_allocation(
    dashboard_app_module,  # noqa: F811
    monkeypatch,
):
    _patch_invoice_detail(
        dashboard_app_module, monkeypatch, view_by_owner={42: DETAIL_VIEW}
    )
    _patch_invoice_savings(dashboard_app_module, monkeypatch)
    client = dashboard_app_module.web.test_client()
    _set_session(client)

    body = client.get("/dashboard/invoices/42").get_data(as_text=True)

    assert "Strom aus dem Quartierakku" not in body


def test_dashboard_invoice_pdf_requires_session(dashboard_app_module):  # noqa: F811
    client = dashboard_app_module.web.test_client()
    response = client.get("/dashboard/invoices/42/pdf")
    assert response.status_code == 401


def test_dashboard_invoice_pdf_missing_and_cross_member_are_identical(
    dashboard_app_module,  # noqa: F811
    monkeypatch,
):
    _patch_invoice_detail(dashboard_app_module, monkeypatch, view_by_owner={})
    client = dashboard_app_module.web.test_client()
    _set_session(client)

    missing = client.get("/dashboard/invoices/999/pdf")
    other_members = client.get("/dashboard/invoices/42/pdf")

    assert missing.status_code == other_members.status_code == 404
    assert missing.get_data() == other_members.get_data()


def test_dashboard_invoice_pdf_route_returns_the_rendered_bytes(
    dashboard_app_module,  # noqa: F811
    monkeypatch,
):
    """The route wiring: fetch the same detail dict as the HTML page, hand it
    to member_invoice_pdf_bytes, and return exactly what comes back."""
    _patch_invoice_detail(
        dashboard_app_module, monkeypatch, view_by_owner={42: DETAIL_VIEW}
    )
    render_pdf = MagicMock(return_value=b"%PDF-fake")
    monkeypatch.setattr(
        dashboard_app_module.dashboard_module, "member_invoice_pdf_bytes", render_pdf
    )
    client = dashboard_app_module.web.test_client()
    _set_session(client)

    response = client.get("/dashboard/invoices/42/pdf")

    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "attachment" in response.headers["Content-Disposition"]
    assert "LEG-2026-000001" in response.headers["Content-Disposition"]
    render_pdf.assert_called_once_with(DETAIL_VIEW)
    assert response.get_data() == b"%PDF-fake"


# === Storage/data failures must be a non-disclosing 503, never 404 ===
#
# A missing or cross-owner invoice_id is a deliberate 404 (see above); a
# storage outage or a corrupted snapshot is a different failure mode and must
# not be reported the same way, or an admin debugging a 404 could mistake a
# database outage for "this invoice does not exist".


def test_dashboard_invoices_list_storage_failure_is_503(
    dashboard_app_module,  # noqa: F811
    monkeypatch,
):
    import database as db

    monkeypatch.setattr(
        dashboard_app_module.dashboard_module,
        "member_invoices_view",
        MagicMock(side_effect=db.BillingStoreError("db down")),
    )
    client = dashboard_app_module.web.test_client()
    _set_session(client)

    response = client.get("/dashboard/invoices")

    assert response.status_code == 503
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_dashboard_invoices_list_corrupted_data_is_503_not_invented_content(
    dashboard_app_module,  # noqa: F811
    monkeypatch,
):
    import member_invoices

    monkeypatch.setattr(
        dashboard_app_module.dashboard_module,
        "member_invoices_view",
        MagicMock(
            side_effect=member_invoices.MemberInvoiceDataError("corrupted invoice")
        ),
    )
    client = dashboard_app_module.web.test_client()
    _set_session(client)

    response = client.get("/dashboard/invoices")

    assert response.status_code == 503
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"


@pytest.mark.parametrize(
    "path", ["/dashboard/invoices/42", "/dashboard/invoices/42/pdf"]
)
def test_dashboard_invoice_detail_and_pdf_storage_failure_is_503(
    dashboard_app_module,  # noqa: F811
    monkeypatch,
    path,
):
    import database as db

    monkeypatch.setattr(
        dashboard_app_module.dashboard_module,
        "member_invoice_detail",
        MagicMock(side_effect=db.BillingStoreError("db down")),
    )
    client = dashboard_app_module.web.test_client()
    _set_session(client)

    response = client.get(path)

    assert response.status_code == 503
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"


@pytest.mark.parametrize(
    "path", ["/dashboard/invoices/42", "/dashboard/invoices/42/pdf"]
)
def test_dashboard_invoice_detail_and_pdf_corrupted_data_is_503(
    dashboard_app_module,  # noqa: F811
    monkeypatch,
    path,
):
    import member_invoices

    monkeypatch.setattr(
        dashboard_app_module.dashboard_module,
        "member_invoice_detail",
        MagicMock(
            side_effect=member_invoices.MemberInvoiceDataError("corrupted invoice")
        ),
    )
    client = dashboard_app_module.web.test_client()
    _set_session(client)

    response = client.get(path)

    assert response.status_code == 503
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"


# === Template accessibility and print contract ===
#
# Assertions target the semantic/accessibility contract (accessible table
# headers, a visible print path, keyboard-focusable controls, a real download
# link), not incidental styling choices such as exact utility class names.


def test_member_invoice_list_template_has_accessible_navigable_entries():
    text = _read_template("member_invoices.html")
    assert 'href="/dashboard/invoices/' in text, (
        "each invoice must be reachable through a real, crawlable/keyboard-"
        "focusable link, not a JS-only click handler"
    )
    assert "onclick" not in text.lower(), (
        "list entries must be navigable without relying on JavaScript"
    )


def test_member_invoice_detail_template_has_accessible_table_semantics():
    text = _read_template("member_invoice_detail.html")
    assert text.count('scope="col"') >= 4, (
        "charge and credit tables must label their columns for screen readers"
    )
    assert "<caption" in text or "aria-label" in text, (
        "each table needs an accessible name (caption or aria-label)"
    )


def test_member_invoice_detail_tables_scroll_on_narrow_screens():
    text = _read_template("member_invoice_detail.html")
    assert text.count('class="overflow-x-auto"') >= 2


def test_dashboard_links_members_to_their_invoices():
    text = _read_template("dashboard.html")
    assert 'href="/dashboard/invoices"' in text
    assert "Meine Rechnungen" in text


def test_member_invoice_detail_template_offers_a_real_pdf_download_link():
    text = _read_template("member_invoice_detail.html")
    assert "/dashboard/invoices/" in text and "/pdf" in text
    assert 'href="/dashboard/invoices/{{ invoice.id }}/pdf"' in text, (
        "the PDF download must be a real link (works without JS, is a "
        "print/save target on its own), not a script-driven action"
    )


def test_member_invoice_detail_template_supports_print_without_hiding_content():
    text = _read_template("member_invoice_detail.html")
    assert "@media print" in text, "the page must define print-specific behaviour"
    assert "window.print()" in text or 'media="print"' in text, (
        "there must be a discoverable, keyboard-reachable print trigger"
    )
    # Content itself (charges/credits/totals) must be server-rendered, so it
    # is present for print/PDF and screen readers even without JavaScript.
    assert "{% for item in invoice" in text or "{% for item in credit_items" in text


def test_member_invoice_detail_print_hides_every_screen_only_control():
    """The printed page must carry no interactive-only furniture (#522)."""
    text = _read_template("member_invoice_detail.html")
    print_block = text.split("@media print", 1)[1]
    assert ".no-print { display: none" in print_block or ".no-print{" in (
        print_block.replace(" ", "")
    ), "the on-screen action bar must be hidden in print"
    assert re.search(r"button\s*{[^}]*display:\s*none", print_block), (
        "every button is on-screen-only on this page, so print must hide buttons "
        "outright, not rely on each button carrying a class"
    )
    assert "attr(href)" not in print_block, (
        "print must not inject raw link URLs into the page; browsers add "
        "addresses as headers or footers on their own"
    )


def test_member_invoice_detail_print_keeps_line_items_and_totals_unbroken():
    """Line items and the totals must not split across a page boundary (#522)."""
    text = _read_template("member_invoice_detail.html")
    print_block = text.split("@media print", 1)[1]
    for selector in ("thead", "tr", "dt", "dd", ".invoice-totals"):
        rule = re.search(
            rf"(?:^|[,;{{}}\n])\s*{re.escape(selector)}\s*[,{{]",
            print_block,
        )
        assert rule, f"{selector} must be addressed by a print rule"
    assert "break-inside: avoid" in print_block or (
        "page-break-inside: avoid" in print_block
    ), "selectors above must carry page-break avoidance"


def test_member_invoice_templates_keep_controls_keyboard_focusable():
    for name in ("member_invoices.html", "member_invoice_detail.html"):
        text = _read_template(name)
        assert "focus-visible" in text, (
            f"{name} must give its interactive controls a visible keyboard focus style"
        )


def test_member_invoice_templates_never_use_en_or_em_dashes():
    """The engineering contract says user-facing German must not use en or em
    dashes; a plain hyphen or "Nicht angegeben" is required instead."""
    for name in ("member_invoices.html", "member_invoice_detail.html"):
        text = _read_template(name)
        assert "–" not in text, f"{name} must not contain an en dash"
        assert "—" not in text, f"{name} must not contain an em dash"


def test_member_invoice_detail_template_uses_natural_swiss_high_german_wording():
    text = _read_template("member_invoice_detail.html")
    assert "Verbrauchskosten" in text
    assert "Verbrauchsladung" not in text


# === Settlement fee (#629) ===


def _fee_invoice_row():
    import copy

    row = copy.deepcopy(INVOICE_ROW)
    row["policy_snapshot"]["settlement_fee_chf_per_kwh"] = "0.020000"
    row["line_items_snapshot"].append(
        {
            "participant_id": "building-session",
            "item_type": "settlement_fee",
            "quantity_kwh": "120.500000",
            "unit_price_chf_per_kwh": "0.020000",
            "amount_chf": "2.410000",
        }
    )
    row["net_chf"] = "14.49"
    row["vat_chf"] = "1.12"
    row["gross_chf"] = "15.61"
    return row


def test_detail_view_renders_the_settlement_fee_from_the_frozen_snapshot(monkeypatch):
    import member_invoices

    monkeypatch.setattr(
        member_invoices.db,
        "get_invoice_for_participant",
        MagicMock(return_value=_fee_invoice_row()),
    )

    view = member_invoices.detail_view(42, "building-session")

    assert len(view["settlement_charges"]) == 1
    fee_item = view["settlement_charges"][0]
    assert fee_item["item_type_label"] == "Abrechnungsentgelt"
    assert fee_item["display_quantity_kwh"] == "120.500"
    assert fee_item["display_unit_price_rp"] == "2.00"
    assert fee_item["display_amount_chf"] == "2.41"
    assert view["display_settlement_fee_rp"] == "2.00"


def test_detail_view_omits_the_settlement_fee_for_legacy_snapshots(monkeypatch):
    import copy

    import member_invoices

    row = copy.deepcopy(INVOICE_ROW)
    monkeypatch.setattr(
        member_invoices.db,
        "get_invoice_for_participant",
        MagicMock(return_value=row),
    )

    view = member_invoices.detail_view(42, "building-session")

    assert view["settlement_charges"] == []
    assert view["display_settlement_fee_rp"] is None


def test_detail_view_fails_closed_on_a_settlement_fee_with_a_foreign_price(
    monkeypatch,
):

    import member_invoices

    row = _fee_invoice_row()
    row["line_items_snapshot"][-1]["unit_price_chf_per_kwh"] = "0.030000"
    monkeypatch.setattr(
        member_invoices.db,
        "get_invoice_for_participant",
        MagicMock(return_value=row),
    )

    with pytest.raises(member_invoices.MemberInvoiceDataError):
        member_invoices.detail_view(42, "building-session")


def test_detail_view_fails_closed_on_a_fee_line_without_a_fee_policy(monkeypatch):
    import copy

    import member_invoices

    row = copy.deepcopy(INVOICE_ROW)
    row["line_items_snapshot"].append(
        {
            "participant_id": "building-session",
            "item_type": "settlement_fee",
            "quantity_kwh": "120.500000",
            "unit_price_chf_per_kwh": "0.000000",
            "amount_chf": "0.000000",
        }
    )
    monkeypatch.setattr(
        member_invoices.db,
        "get_invoice_for_participant",
        MagicMock(return_value=row),
    )

    with pytest.raises(member_invoices.MemberInvoiceDataError):
        member_invoices.detail_view(42, "building-session")


def test_member_invoice_pdf_renders_the_settlement_fee_section(monkeypatch):
    import member_invoices

    monkeypatch.setattr(
        member_invoices.db,
        "get_invoice_for_participant",
        MagicMock(return_value=_fee_invoice_row()),
    )

    invoice = member_invoices.detail_view(42, "building-session")

    with patch.object(
        member_invoices.document_generator,
        "_render_pdf",
        return_value=b"%PDF-fake",
    ) as render_pdf:
        member_invoices.render_pdf(invoice)

    pdf_html = render_pdf.call_args.args[0]

    assert "Abrechnungsentgelt" in pdf_html
    assert "2.41" in pdf_html


def test_settlement_fee_section_is_hidden_for_legacy_invoices():
    template = _read_template("member_invoice_detail.html")

    assert "settlement_charges" in template
    assert "{% if invoice.settlement_charges %}" in template


# === Quartierakku cost share (#628) ===


def _battery_invoice_row():
    import copy

    row = copy.deepcopy(INVOICE_ROW)
    row["line_items_snapshot"].append(
        {
            "participant_id": "building-session",
            "item_type": "battery_cost_share",
            "quantity_kwh": None,
            "unit_price_chf_per_kwh": None,
            "amount_chf": "120.00",
        }
    )
    row["net_chf"] = "132.08"
    row["vat_chf"] = "10.17"
    row["gross_chf"] = "142.25"
    return row


def test_detail_view_shows_the_quartierakku_share(monkeypatch):
    import member_invoices

    monkeypatch.setattr(
        member_invoices.db,
        "get_invoice_for_participant",
        MagicMock(return_value=_battery_invoice_row()),
    )

    view = member_invoices.detail_view(42, "building-session")

    assert len(view["battery_shares"]) == 1
    share = view["battery_shares"][0]
    assert share["item_type_label"] == "Quartierakku-Anteil"
    assert share["display_quantity_kwh"] is None
    assert share["display_unit_price_rp"] is None
    assert share["display_amount_chf"] == "120.00"


def test_detail_view_omits_the_battery_block_without_battery_lines(monkeypatch):
    import member_invoices

    monkeypatch.setattr(
        member_invoices.db,
        "get_invoice_for_participant",
        MagicMock(return_value=INVOICE_ROW),
    )

    view = member_invoices.detail_view(42, "building-session")

    assert view["battery_shares"] == []


def test_detail_view_fails_closed_on_a_battery_share_with_a_quantity(monkeypatch):
    import member_invoices

    row = _battery_invoice_row()
    row["line_items_snapshot"][-1]["quantity_kwh"] = "5"
    monkeypatch.setattr(
        member_invoices.db,
        "get_invoice_for_participant",
        MagicMock(return_value=row),
    )

    with pytest.raises(member_invoices.MemberInvoiceDataError):
        member_invoices.detail_view(42, "building-session")


def test_member_invoice_pdf_renders_the_battery_share_section(monkeypatch):
    import member_invoices

    monkeypatch.setattr(
        member_invoices.db,
        "get_invoice_for_participant",
        MagicMock(return_value=_battery_invoice_row()),
    )

    invoice = member_invoices.detail_view(42, "building-session")

    with patch.object(
        member_invoices.document_generator,
        "_render_pdf",
        return_value=b"%PDF-fake",
    ) as render_pdf:
        member_invoices.render_pdf(invoice)

    pdf_html = render_pdf.call_args.args[0]

    assert "Quartierakku-Anteil" in pdf_html
    assert "120.00" in pdf_html


def test_battery_share_section_is_hidden_for_legacy_invoices():
    template = _read_template("member_invoice_detail.html")

    assert "{% if invoice.battery_shares %}" in template


@pytest.mark.parametrize(
    "field", ["policy_snapshot", "provenance_snapshot", "line_items_snapshot"]
)
def test_detail_accepts_json_encoded_frozen_snapshots(monkeypatch, field):
    import member_invoices

    row = copy.deepcopy(INVOICE_ROW)
    row[field] = json.dumps(row[field])
    monkeypatch.setattr(
        member_invoices.db, "get_invoice_for_participant", lambda *_: row
    )

    view = member_invoices.detail_view(42, "building-session")

    assert view["display_gross_chf"] == "13.01"
    assert view["charges"][0]["display_quantity_kwh"] == "120.500"
    assert view["period_label"] == "Juli 2026"


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("id",), True, "Die Rechnung hat keine gültige ID."),
        (("id",), 0, "Die Rechnung hat keine gültige ID."),
        (("invoice_number",), " ", "Die Rechnung hat keine gültige Rechnungsnummer."),
        (("participant_id",), "", "Die Rechnung hat keine gültige Teilnehmer-ID."),
        (
            ("participant_id",),
            "someone-else",
            "Die Rechnung hat eine ungültige Zuordnung.",
        ),
        (("issue_date",), "invalid", "Die Rechnung hat kein gültiges Rechnungsdatum."),
        (("due_date",), False, "Die Rechnung hat kein gültiges Fälligkeitsdatum."),
        (
            ("policy_snapshot",),
            "{",
            "Die Rechnung hat keine gültige Richtlinien-Kopie.",
        ),
        (("policy_snapshot",), {}, "Die Rechnung hat keine gültige Richtlinien-Kopie."),
        (
            ("policy_snapshot",),
            [1],
            "Die Rechnung hat keine gültige Richtlinien-Kopie.",
        ),
        (
            ("policy_snapshot", "vat_mode"),
            "invalid",
            "Die Rechnung hat einen ungültigen Mehrwertsteuer-Modus.",
        ),
        (
            ("policy_snapshot", "vat_rate_pct"),
            "NaN",
            "Die Richtlinien-Kopie hat keinen gültigen Mehrwertsteuersatz.",
        ),
        (
            ("policy_snapshot", "internal_price_chf_per_kwh"),
            "NaN",
            "Die Richtlinien-Kopie hat keinen gültigen Tarifpreis.",
        ),
        (
            ("policy_snapshot", "payment_days"),
            True,
            "Die Richtlinien-Kopie hat keine gültige Zahlungsfrist.",
        ),
        (
            ("policy_snapshot", "payment_days"),
            30.0,
            "Die Richtlinien-Kopie hat keine gültige Zahlungsfrist.",
        ),
        (
            ("policy_snapshot", "settlement_fee_chf_per_kwh"),
            "NaN",
            "Die Richtlinien-Kopie hat ein ungültiges Abrechnungsentgelt.",
        ),
        (
            ("policy_snapshot", "settlement_fee_chf_per_kwh"),
            "-0.01",
            "Die Richtlinien-Kopie hat ein ungültiges Abrechnungsentgelt.",
        ),
        (
            ("provenance_snapshot",),
            "{",
            "Die Rechnung hat keine gültige Periodenangabe.",
        ),
        (
            ("provenance_snapshot", "period_start"),
            "invalid",
            "Die Rechnung hat keine gültige Periodenangabe.",
        ),
        (
            ("provenance_snapshot", "period_end"),
            "",
            "Die Rechnung hat keine gültige Periodenangabe.",
        ),
        (
            ("provenance_snapshot", "period_end"),
            "2026-07-01",
            "Die Datumsangaben der Rechnung sind inkonsistent.",
        ),
        (("community_id",), None, "Die Rechnung hat keinen gültigen Aussteller."),
        (
            ("provenance_snapshot", "issuer", "name"),
            " ",
            "Die Rechnung hat keinen gültigen Aussteller.",
        ),
        (
            ("provenance_snapshot", "battery_source"),
            [1],
            "Die Quartierakku-Angabe dieser Rechnung ist ungültig.",
        ),
        (
            ("provenance_snapshot", "battery_source"),
            {"battery_kwh": "NaN", "value_chf": "1"},
            "Die Quartierakku-Angabe dieser Rechnung ist ungültig.",
        ),
        (
            ("provenance_snapshot", "battery_source"),
            {"battery_kwh": "1", "value_chf": "NaN"},
            "Die Quartierakku-Angabe dieser Rechnung ist ungültig.",
        ),
        (
            ("provenance_snapshot", "battery_source"),
            {"battery_kwh": "1", "value_chf": "-1"},
            "Die Quartierakku-Angabe dieser Rechnung ist ungültig.",
        ),
        (("line_items_snapshot",), "[", "Die Rechnung hat keine gültigen Positionen."),
        (("line_items_snapshot",), {}, "Die Rechnung hat keine gültigen Positionen."),
        (
            ("line_items_snapshot",),
            {"not": "a list"},
            "Die Rechnung hat keine gültigen Positionen.",
        ),
        (("line_items_snapshot",), [], "Die Rechnung hat keine gültigen Positionen."),
        (
            ("line_items_snapshot", 0),
            "invalid",
            "Eine Rechnungsposition ist fehlerhaft.",
        ),
        (
            ("line_items_snapshot", 0, "item_type"),
            "invalid",
            "Eine Rechnungsposition hat einen ungültigen Typ.",
        ),
        (
            ("line_items_snapshot", 0, "participant_id"),
            "other",
            "Eine Rechnungsposition gehört nicht zu dieser Rechnung.",
        ),
        (
            ("line_items_snapshot", 0, "amount_chf"),
            "NaN",
            "Eine Rechnungsposition hat keinen gültigen Betrag.",
        ),
        (
            ("line_items_snapshot", 0, "quantity_kwh"),
            "NaN",
            "Eine Rechnungsposition hat keine gültige Menge.",
        ),
        (
            ("line_items_snapshot", 0, "unit_price_chf_per_kwh"),
            "NaN",
            "Eine Rechnungsposition hat keinen gültigen Preis.",
        ),
        (
            ("line_items_snapshot", 0, "quantity_kwh"),
            "-1",
            "Eine Rechnungsposition stimmt nicht mit der Richtlinie überein.",
        ),
        (
            ("line_items_snapshot", 0, "unit_price_chf_per_kwh"),
            "0.2",
            "Eine Rechnungsposition stimmt nicht mit der Richtlinie überein.",
        ),
        (
            ("line_items_snapshot", 0, "quantity_kwh"),
            "1e40",
            "Der Betrag einer Rechnungsposition ist nicht berechenbar.",
        ),
        (
            ("line_items_snapshot", 0, "amount_chf"),
            "1",
            "Der Betrag einer Rechnungsposition ist rechnerisch inkonsistent.",
        ),
        (
            ("vat_rate_pct",),
            "NaN",
            "Die Rechnung hat keinen gültigen Mehrwertsteuersatz.",
        ),
        (
            ("vat_rate_pct",),
            "8.1",
            "Die Mehrwertsteuer stimmt nicht mit der Richtlinien-Kopie überein.",
        ),
        (("net_chf",), "NaN", "Die Rechnung hat keinen gültigen Nettobetrag."),
        (
            ("net_chf",),
            "12.07",
            "Die Rechnungssumme stimmt nicht mit den Positionen überein.",
        ),
        (("vat_chf",), "NaN", "Die Rechnung hat keinen gültigen Mehrwertsteuerbetrag."),
        (("vat_chf",), "0.92", "Die Rechnungssummen sind rechnerisch inkonsistent."),
        (("gross_chf",), "NaN", "Die Rechnung hat keinen gültigen Gesamtbetrag."),
    ],
)
def test_detail_explains_why_a_frozen_invoice_cannot_be_displayed(
    monkeypatch, path, value, message
):
    import member_invoices

    row = copy.deepcopy(INVOICE_ROW)
    node = row
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    monkeypatch.setattr(
        member_invoices.db, "get_invoice_for_participant", lambda *_: row
    )

    with pytest.raises(member_invoices.MemberInvoiceDataError) as exc:
        member_invoices.detail_view(42, "building-session")

    assert str(exc.value) == message


@pytest.mark.parametrize("payment_days", [1, 365])
def test_detail_accepts_zero_energy_zero_prices_and_payment_boundaries(
    monkeypatch, payment_days
):
    from datetime import UTC, date, datetime, timedelta

    import member_invoices

    row = copy.deepcopy(INVOICE_ROW)
    row["line_items_snapshot"] = [row["line_items_snapshot"][0]]
    row["line_items_snapshot"][0].update(
        quantity_kwh="0", unit_price_chf_per_kwh="0", amount_chf="0"
    )
    row["policy_snapshot"].update(
        internal_price_chf_per_kwh="0",
        grid_fee_chf_per_kwh="0",
        settlement_fee_chf_per_kwh="0",
        payment_days=payment_days,
        vat_mode="none",
        vat_rate_pct="0",
    )
    row.update(
        net_chf="0",
        vat_rate_pct="0",
        vat_chf="0",
        gross_chf="0",
        issue_date=datetime(2026, 8, 1, 0, 0, tzinfo=UTC),
        due_date=date(2026, 8, 1) + timedelta(days=payment_days),
    )
    row["provenance_snapshot"]["battery_source"] = {
        "battery_kwh": "0",
        "value_chf": "0",
    }
    monkeypatch.setattr(
        member_invoices.db, "get_invoice_for_participant", lambda *_: row
    )

    view = member_invoices.detail_view(42, "building-session")

    assert view["issue_date"] == "2026-08-01"
    assert view["period_start"] == "2026-07-01"
    assert view["period_end"] == "2026-08-01"
    assert view["display_vat_rate_pct"] == "0.00"
    assert view["display_gross_chf"] == "0.00"
    assert view["display_grid_fee_rp"] == "0.00"
    assert view["display_settlement_fee_rp"] == "0.00"
    assert view["display_battery_kwh"] == "0.000"
    assert view["display_battery_value_chf"] == "0.00"
    assert view["rounding_adjustments"] == []
    assert view["charges"] == [
        {
            "item_type": "consumer_charge",
            "item_type_label": "Verbrauchskosten",
            "display_quantity_kwh": "0.000",
            "display_unit_price_rp": "0.00",
            "display_amount_chf": "0.00",
        }
    ]


def test_detail_rounds_money_and_vat_half_up(monkeypatch):
    import member_invoices

    row = copy.deepcopy(INVOICE_ROW)
    row["line_items_snapshot"] = [row["line_items_snapshot"][0]]
    row["line_items_snapshot"][0].update(quantity_kwh="0.333333", amount_chf="0.050000")
    row["policy_snapshot"]["vat_rate_pct"] = "10"
    row.update(net_chf="0.05", vat_rate_pct="10", vat_chf="0.01", gross_chf="0.06")
    monkeypatch.setattr(
        member_invoices.db, "get_invoice_for_participant", lambda *_: row
    )

    view = member_invoices.detail_view(42, "building-session")

    assert view["display_net_chf"] == "0.05"
    assert view["display_vat_chf"] == "0.01"
    assert view["display_gross_chf"] == "0.06"
    assert view["display_vat_rate_pct"] == "10.00"


def test_list_preserves_the_frozen_invoice_correction_links(monkeypatch):
    import member_invoices

    row = copy.deepcopy(INVOICE_ROW)
    row.update(
        corrects_invoice_number="LEG-2026-000009",
        corrected_by_invoice_number="LEG-2026-000011",
    )
    monkeypatch.setattr(
        member_invoices.db, "get_invoices_for_participant", lambda *_: [row]
    )

    view = member_invoices.list_view("building-session")["invoices"][0]

    assert view["corrects_invoice_number"] == "LEG-2026-000009"
    assert view["corrected_by_invoice_number"] == "LEG-2026-000011"


def _rounded_invoice():
    row = copy.deepcopy(INVOICE_ROW)
    row["line_items_snapshot"].append(
        {
            "participant_id": "building-session",
            "item_type": "rounding_adjustment",
            "quantity_kwh": None,
            "unit_price_chf_per_kwh": None,
            "amount_chf": "0.01",
        }
    )
    row["provenance_snapshot"].pop("rounding_adjustment")
    row["provenance_snapshot"]["reconciliation"] = {
        "production_per_participant": {"building-session": {}}
    }
    row.update(net_chf="12.09", vat_chf="0.93", gross_chf="13.02")
    return row


@pytest.mark.parametrize("with_proof", [False, True])
def test_detail_accepts_a_cent_rounding_residual_and_keeps_it_separate(
    monkeypatch, with_proof
):
    import member_invoices

    row = _rounded_invoice()
    if with_proof:
        row["provenance_snapshot"]["rounding_adjustment"] = {
            "participant_id": "building-session",
            "amount_chf": "0.01",
        }
    monkeypatch.setattr(
        member_invoices.db, "get_invoice_for_participant", lambda *_: row
    )

    view = member_invoices.detail_view(42, "building-session")

    assert view["rounding_adjustments"] == [
        {
            "item_type": "rounding_adjustment",
            "item_type_label": "Rundungsausgleich",
            "display_quantity_kwh": None,
            "display_unit_price_rp": None,
            "display_amount_chf": "0.01",
        }
    ]
    assert len(view["credits"]) == 1
    assert view["display_gross_chf"] == "13.02"


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (
            ("line_items_snapshot", 2, "quantity_kwh"),
            "0",
            "Ein Rundungsausgleich darf weder Menge noch Preis enthalten.",
        ),
        (
            ("line_items_snapshot", 2, "unit_price_chf_per_kwh"),
            "0",
            "Ein Rundungsausgleich darf weder Menge noch Preis enthalten.",
        ),
        (
            ("provenance_snapshot", "rounding_adjustment"),
            [],
            "Der Rundungsausgleich ist nicht zulässig.",
        ),
        (
            ("provenance_snapshot", "rounding_adjustment"),
            {"participant_id": "building-session", "amount_chf": "NaN"},
            "Der Rundungsausgleich ist nicht zulässig.",
        ),
        (
            ("provenance_snapshot", "reconciliation"),
            {"production_per_participant": [1]},
            "Der Rundungsausgleich ist nicht zulässig.",
        ),
        (
            ("provenance_snapshot", "reconciliation"),
            {"production_per_participant": {"": {}}},
            "Der Rundungsausgleich ist nicht zulässig.",
        ),
        (
            ("provenance_snapshot", "reconciliation"),
            {"production_per_participant": {"aaa": {}, "building-session": {}}},
            "Der Rundungsausgleich ist nicht zulässig.",
        ),
        (
            ("line_items_snapshot", 2, "amount_chf"),
            "0.010001",
            "Der Rundungsausgleich übersteigt den zulässigen Restbetrag.",
        ),
    ],
)
def test_detail_explains_invalid_rounding_evidence(monkeypatch, path, value, message):
    import member_invoices

    row = _rounded_invoice()
    node = row
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    monkeypatch.setattr(
        member_invoices.db, "get_invoice_for_participant", lambda *_: row
    )

    with pytest.raises(member_invoices.MemberInvoiceDataError) as exc:
        member_invoices.detail_view(42, "building-session")

    assert str(exc.value) == message


class _PdfVisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_style = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "style":
            self.in_style = True

    def handle_endtag(self, tag):
        if tag == "style":
            self.in_style = False

    def handle_data(self, data):
        if not self.in_style and data.strip():
            self.parts.append(data.strip())


def test_pdf_visible_values_include_charges_credits_and_rounding():
    import member_invoices

    view = copy.deepcopy(DETAIL_VIEW)
    view["rounding_adjustments"] = [
        {
            "item_type_label": "Rundungsausgleich",
            "display_quantity_kwh": None,
            "display_unit_price_rp": None,
            "display_amount_chf": "0.01",
        }
    ]
    parser = _PdfVisibleText()
    parser.feed(member_invoices.render_pdf_html_string(view))

    assert parser.parts == [
        "Rechnung LEG-2026-000001",
        "Aussteller:",
        "LEG Musterweg",
        "Periode:",
        "Juli 2026",
        "Rechnungsdatum:",
        "2026-08-05",
        "Fällig am:",
        "2026-09-04",
        "Interner Preis:",
        "15.00 Rp./kWh",
        "Netzentgelt:",
        "8.00 Rp./kWh",
        "Zahlungsfrist:",
        "30 Tage",
        "Verbrauchskosten",
        "Position",
        "Menge (kWh)",
        "Preis (Rp./kWh)",
        "Betrag (CHF)",
        "Verbrauchskosten",
        "120.500",
        "15.00",
        "18.08",
        "Gutschriften und Ausgleich",
        "Position",
        "Menge (kWh)",
        "Preis (Rp./kWh)",
        "Betrag (CHF)",
        "Produzentengutschrift",
        "40.000",
        "15.00",
        "-6.00",
        "Rundungsausgleich",
        "-",
        "-",
        "0.01",
        "Mehrwertsteuer",
        "Mehrwertsteuer ausweisen (7.70%)",
        "Total",
        "Nettobetrag",
        "12.08 CHF",
        "Mehrwertsteuer",
        "0.93 CHF",
        "Gesamtbetrag",
        "13.01 CHF",
        "Erstellt mit OpenLEG",
    ]


def test_detail_rounds_display_values_independently_of_decimal_context(monkeypatch):
    from decimal import ROUND_DOWN, localcontext

    import member_invoices

    monkeypatch.setattr(
        member_invoices.db,
        "get_invoice_for_participant",
        lambda *_: copy.deepcopy(INVOICE_ROW),
    )
    with localcontext() as context:
        context.rounding = ROUND_DOWN
        view = member_invoices.detail_view(42, "building-session")

    assert view["charges"][0]["display_amount_chf"] == "18.08"
    assert view["display_gross_chf"] == "13.01"


@pytest.mark.parametrize(
    ("rate", "vat", "gross"), [("0.5", "0.06", "12.14"), ("100", "12.08", "24.16")]
)
def test_detail_accepts_positive_vat_at_the_allowed_boundaries(
    monkeypatch, rate, vat, gross
):
    import member_invoices

    row = copy.deepcopy(INVOICE_ROW)
    row["policy_snapshot"]["vat_rate_pct"] = rate
    row.update(vat_rate_pct=rate, vat_chf=vat, gross_chf=gross)
    monkeypatch.setattr(
        member_invoices.db, "get_invoice_for_participant", lambda *_: row
    )

    view = member_invoices.detail_view(42, "building-session")

    assert view["display_vat_chf"] == vat
    assert view["display_gross_chf"] == gross


@pytest.mark.parametrize(
    "problem",
    [
        "quantity",
        "price",
        "duplicate",
        "double_rounding",
        "no_producer",
        "empty_production",
        "bad_producer_id",
        "overflow",
    ],
)
def test_detail_explains_remaining_position_and_source_failures(monkeypatch, problem):
    import member_invoices

    row = _rounded_invoice()
    if problem in ("quantity", "price"):
        row = _battery_invoice_row()
        share = next(
            item
            for item in row["line_items_snapshot"]
            if item["item_type"] == "battery_cost_share"
        )
        share["quantity_kwh" if problem == "quantity" else "unit_price_chf_per_kwh"] = (
            "0"
        )
        message = "Ein Quartierakku-Anteil darf weder Menge noch Preis enthalten."
    elif problem == "duplicate":
        row["line_items_snapshot"].append(copy.deepcopy(row["line_items_snapshot"][0]))
        message = "Die Rechnung enthält doppelte Positionen."
    elif problem == "double_rounding":
        row["line_items_snapshot"].append(copy.deepcopy(row["line_items_snapshot"][-1]))
        message = "Der Rundungsausgleich ist nicht zulässig."
    elif problem == "no_producer":
        row["line_items_snapshot"].pop(1)
        message = "Der Rundungsausgleich ist nicht zulässig."
    elif problem == "empty_production":
        row["provenance_snapshot"]["reconciliation"]["production_per_participant"] = {}
        message = "Der Rundungsausgleich ist nicht zulässig."
    elif problem == "bad_producer_id":
        row["provenance_snapshot"]["reconciliation"]["production_per_participant"] = {
            42: {},
            "building-session": {},
        }
        # Invalid ids do not nominate a producer; the valid frozen id still does.
        message = None
    else:
        row["provenance_snapshot"]["battery_source"] = {
            "battery_kwh": "1e40",
            "value_chf": "1",
        }
        message = "Ein Betrag der Rechnung ist ungültig."
    monkeypatch.setattr(
        member_invoices.db, "get_invoice_for_participant", lambda *_: row
    )

    if message is None:
        assert (
            member_invoices.detail_view(42, "building-session")["display_gross_chf"]
            == "13.02"
        )
    else:
        with pytest.raises(member_invoices.MemberInvoiceDataError) as exc:
            member_invoices.detail_view(42, "building-session")
        assert str(exc.value) == message


def test_detail_preserves_the_invoice_lifecycle_error(monkeypatch):
    import member_invoices

    row = copy.deepcopy(INVOICE_ROW)
    row["lifecycle_state"] = "invalid"
    monkeypatch.setattr(
        member_invoices.db, "get_invoice_for_participant", lambda *_: row
    )

    with pytest.raises(member_invoices.MemberInvoiceDataError) as exc:
        member_invoices.detail_view(42, "building-session")

    assert str(exc.value) == "Die Rechnung hat einen ungültigen Status."


@pytest.mark.parametrize("problem", ["production_list", "vat_exempt", "sum_overflow"])
def test_detail_refuses_inconsistent_frozen_evidence_before_display(
    monkeypatch, problem
):
    import member_invoices

    row = _rounded_invoice()
    if problem == "production_list":
        row["provenance_snapshot"]["reconciliation"]["production_per_participant"] = [
            "building-session"
        ]
        message = "Der Rundungsausgleich ist nicht zulässig."
    elif problem == "vat_exempt":
        row["policy_snapshot"]["vat_mode"] = "none"
        message = "Die Rechnung hat einen ungültigen Mehrwertsteuersatz."
    else:
        row["line_items_snapshot"][-1]["amount_chf"] = "90000000000000000000000000"
        row["line_items_snapshot"].append(
            {
                "participant_id": "building-session",
                "item_type": "battery_cost_share",
                "quantity_kwh": None,
                "unit_price_chf_per_kwh": None,
                "amount_chf": "90000000000000000000000000",
            }
        )
        row["provenance_snapshot"]["rounding_adjustment"] = {
            "participant_id": "building-session",
            "amount_chf": "90000000000000000000000000",
        }
        message = "Die Rechnungssumme ist nicht berechenbar."
    monkeypatch.setattr(
        member_invoices.db, "get_invoice_for_participant", lambda *_: row
    )

    with pytest.raises(member_invoices.MemberInvoiceDataError) as exc:
        member_invoices.detail_view(42, "building-session")

    assert str(exc.value) == message


def test_pdf_explains_empty_sections_and_omits_an_unknown_legacy_grid_fee():
    import member_invoices

    view = copy.deepcopy(DETAIL_VIEW)
    view.update(charges=[], credits=[], rounding_adjustments=[])
    view["display_grid_fee_rp"] = None
    parser = _PdfVisibleText()
    parser.feed(member_invoices.render_pdf_html_string(view))

    assert parser.parts == [
        "Rechnung LEG-2026-000001",
        "Aussteller:",
        "LEG Musterweg",
        "Periode:",
        "Juli 2026",
        "Rechnungsdatum:",
        "2026-08-05",
        "Fällig am:",
        "2026-09-04",
        "Interner Preis:",
        "15.00 Rp./kWh",
        "Zahlungsfrist:",
        "30 Tage",
        "Verbrauchskosten",
        "Position",
        "Menge (kWh)",
        "Preis (Rp./kWh)",
        "Betrag (CHF)",
        "Keine Verbrauchskosten",
        "Gutschriften und Ausgleich",
        "Position",
        "Menge (kWh)",
        "Preis (Rp./kWh)",
        "Betrag (CHF)",
        "Keine Gutschriften",
        "Mehrwertsteuer",
        "Mehrwertsteuer ausweisen (7.70%)",
        "Total",
        "Nettobetrag",
        "12.08 CHF",
        "Mehrwertsteuer",
        "0.93 CHF",
        "Gesamtbetrag",
        "13.01 CHF",
        "Erstellt mit OpenLEG",
    ]


def test_detail_explains_an_overflowing_finite_vat_amount(monkeypatch):
    import member_invoices

    row = copy.deepcopy(INVOICE_ROW)
    row["vat_chf"] = "1e1000000"
    monkeypatch.setattr(
        member_invoices.db, "get_invoice_for_participant", lambda *_: row
    )

    with pytest.raises(member_invoices.MemberInvoiceDataError) as exc:
        member_invoices.detail_view(42, "building-session")

    assert str(exc.value) == "Die Rechnungssummen sind nicht berechenbar."
