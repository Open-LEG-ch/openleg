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
