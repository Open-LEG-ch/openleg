# SPDX-License-Identifier: AGPL-3.0-or-later
"""Behavioral contract for #630: realized member savings from metered data.

The member sees what the LEG membership actually returned for a billed
period: consumption and the locally covered share from their own E66
readings, and the saving the frozen policy snapshot prices, never a tariff
re-derived at render time. A period without a complete consumption series
renders the explicit empty state. Another member's invoice id is
indistinguishable from a missing one, and their metered numbers are never
read at all.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.test_dashboard_access_routes import _set_session
from tests.test_dashboard_access_routes import (  # noqa: F401
    app_module as dashboard_app_module,
)

PERIOD_START = "2026-07-01T00:00:00+02:00"
PERIOD_END = "2026-07-01T01:00:00+02:00"
POINT = "CH000000000000000000000000000001"

POLICY_SNAPSHOT = {
    "vat_mode": "none",
    "vat_rate_pct": "0",
    "internal_price_chf_per_kwh": "0.150000",
    "grid_fee_chf_per_kwh": "0.080000",
    "network_level": "same",
    "distribution_model": "proportional",
    "payment_days": 30,
}

PROVENANCE_SNAPSHOT = {
    "period_start": PERIOD_START,
    "period_end": PERIOD_END,
    "input_fingerprint": "fingerprint-1",
    "source_document_ids": ["doc-1"],
    "reconciliation": {
        "per_participant": {
            "building-session": {
                "vnb_kwh": 1.0,
                "engine_kwh": 1.0,
                "difference_kwh": 0.0,
            }
        }
    },
    "rounding_adjustment": None,
}

INVOICE_ROW = {
    "id": 42,
    "community_id": "community-a",
    "participant_id": "building-session",
    "invoice_number": "LEG-2026-000001",
    "policy_snapshot": dict(POLICY_SNAPSHOT),
    "provenance_snapshot": dict(PROVENANCE_SNAPSHOT),
    "line_items_snapshot": [
        {
            "participant_id": "building-session",
            "item_type": "consumer_charge",
            "quantity_kwh": "1.000000",
            "unit_price_chf_per_kwh": "0.150000",
            "amount_chf": "0.150000",
        }
    ],
    "net_chf": "0.15",
    "vat_rate_pct": "0",
    "vat_chf": "0.00",
    "gross_chf": "0.15",
    "issue_date": "2026-08-05",
    "due_date": "2026-09-04",
    "status": "issued",
}


def _reading(offset_minutes, total="0.500", community="0.250", direction="consumption"):
    return {
        "metering_point_id": POINT,
        "direction": direction,
        "measured_at": datetime(
            2026, 6, 30, 22, 0, tzinfo=timezone.utc
        ) + timedelta(minutes=offset_minutes),
        "resolution_minutes": 15,
        "total_kwh": Decimal(total),
        "grid_kwh": Decimal(total) - Decimal(community),
        "community_kwh": Decimal(community),
        "condition_code": None,
    }


def _complete_readings():
    return [_reading(15 * index) for index in range(4)]


# === Pure view-builder: hand-checked fixture ===


def test_realized_savings_computes_hand_checked_numbers():
    import member_savings

    view = member_savings.realized_savings(
        _complete_readings(), PROVENANCE_SNAPSHOT, POLICY_SNAPSHOT
    )

    assert view["available"] is True
    assert view["consumption_kwh"] == Decimal("2.000")
    assert view["local_kwh"] == Decimal("1.000")
    assert view["display_consumption_kwh"] == "2.000"
    assert view["display_local_kwh"] == "1.000"
    assert view["display_local_share_pct"] == "50.0"
    # 1.000 kWh lokal gedeckt mal 8.00 Rp./kWh Netzentgelt mal 40 Prozent
    # Ermässigung auf derselben Netzebene.
    assert view["display_savings_chf"] == "0.03"
    assert view["display_grid_fee_rp"] == "8.00"
    assert view["network_level_label"] == "Gleiche Netzebene"


def test_realized_savings_uses_cross_level_discount_from_the_policy():
    import member_savings

    policy = {**POLICY_SNAPSHOT, "network_level": "cross"}

    view = member_savings.realized_savings(
        _complete_readings(), PROVENANCE_SNAPSHOT, policy
    )

    # 1.000 kWh mal 8.00 Rp./kWh mal 20 Prozent auf unterschiedlicher Ebene.
    assert view["display_savings_chf"] == "0.02"
    assert view["network_level_label"] == "Unterschiedliche Netzebenen"


def test_realized_savings_reports_zero_consumption_without_a_share():
    import member_savings

    readings = [_reading(15 * index, total="0.000", community="0.000") for index in range(4)]

    view = member_savings.realized_savings(
        readings, PROVENANCE_SNAPSHOT, POLICY_SNAPSHOT
    )

    assert view["available"] is True
    assert view["consumption_kwh"] == Decimal("0.000")
    assert view["display_local_share_pct"] is None
    assert view["display_savings_chf"] == "0.00"


def test_realized_savings_ignores_production_rows():
    import member_savings

    readings = _complete_readings() + [
        _reading(15 * index, total="0.300", community="0.000", direction="production")
        for index in range(4)
    ]

    view = member_savings.realized_savings(
        readings, PROVENANCE_SNAPSHOT, POLICY_SNAPSHOT
    )

    assert view["display_consumption_kwh"] == "2.000"
    assert view["display_local_kwh"] == "1.000"


# === Empty state: incomplete metering says so instead of guessing ===


def test_realized_savings_renders_empty_state_without_readings():
    import member_savings

    view = member_savings.realized_savings(
        [], PROVENANCE_SNAPSHOT, POLICY_SNAPSHOT
    )

    assert view["available"] is False
    assert view["message"] == member_savings.EMPTY_STATE_MESSAGE
    assert view["period_label"] == "Juli 2026"
    assert "display_savings_chf" not in view


def test_realized_savings_renders_empty_state_for_gap_in_the_series():
    import member_savings

    readings = [_reading(15 * index) for index in range(3)]

    view = member_savings.realized_savings(
        readings, PROVENANCE_SNAPSHOT, POLICY_SNAPSHOT
    )

    assert view["available"] is False
    assert view["message"] == member_savings.EMPTY_STATE_MESSAGE


def test_realized_savings_renders_empty_state_for_duplicated_intervals():
    import member_savings

    readings = [_reading(0), _reading(15), _reading(30), _reading(30)]

    view = member_savings.realized_savings(
        readings, PROVENANCE_SNAPSHOT, POLICY_SNAPSHOT
    )

    assert view["available"] is False


# === Fail-closed on corrupted billing or metering data ===


@pytest.mark.parametrize(
    ("policy", "reason"),
    [
        ({**POLICY_SNAPSHOT, "network_level": "bogus"}, "invalid network level"),
        ({**POLICY_SNAPSHOT, "grid_fee_chf_per_kwh": "-0.08"}, "negative grid fee"),
        ({**POLICY_SNAPSHOT, "grid_fee_chf_per_kwh": "NaN"}, "non-finite grid fee"),
        ({}, "empty policy snapshot"),
    ],
)
def test_realized_savings_fails_closed_on_corrupted_policy(policy, reason):
    import member_savings

    with pytest.raises(member_savings.MemberSavingsDataError):
        member_savings.realized_savings(
            _complete_readings(), PROVENANCE_SNAPSHOT, policy
        )


def test_realized_savings_fails_closed_on_negative_readings():
    import member_savings

    readings = [_reading(15 * index, total="-0.500") for index in range(4)]

    with pytest.raises(member_savings.MemberSavingsDataError):
        member_savings.realized_savings(
            readings, PROVENANCE_SNAPSHOT, POLICY_SNAPSHOT
        )


def test_realized_savings_fails_closed_when_community_exceeds_total():
    import member_savings

    readings = [_reading(15 * index, community="0.900") for index in range(4)]

    with pytest.raises(member_savings.MemberSavingsDataError):
        member_savings.realized_savings(
            readings, PROVENANCE_SNAPSHOT, POLICY_SNAPSHOT
        )


def test_realized_savings_fails_closed_on_missing_period_bounds():
    import member_savings

    with pytest.raises(member_savings.MemberSavingsDataError):
        member_savings.realized_savings(
            _complete_readings(), {"period_start": PERIOD_START}, POLICY_SNAPSHOT
        )


# === Invoice agreement: same period, same frozen tariff ===


def test_invoice_savings_view_agrees_with_the_invoice_totals(monkeypatch):
    import member_savings

    lookup = MagicMock(return_value=dict(INVOICE_ROW))
    fetch_readings = MagicMock(return_value=_complete_readings())
    monkeypatch.setattr(
        member_savings.db, "get_invoice_for_participant", lookup
    )
    monkeypatch.setattr(
        member_savings.db, "get_building_period_readings", fetch_readings
    )

    view = member_savings.invoice_savings_view(42, "building-session")

    lookup.assert_called_once_with(42, "building-session")
    # Die Messwerte werden nur für das eigene Gebäude geladen.
    fetch_readings.assert_called_once_with(
        "building-session",
        datetime.fromisoformat(PERIOD_START),
        datetime.fromisoformat(PERIOD_END),
    )
    charged = INVOICE_ROW["line_items_snapshot"][0]
    assert view["display_local_kwh"] == "1.000"
    assert Decimal(charged["quantity_kwh"]) == Decimal(view["local_kwh"])
    assert view["display_consumption_kwh"] == "2.000"
    assert view["period_label"] == "Juli 2026"
    # Der Tarif stammt aus der eingefrorenen Richtlinien-Kopie der Rechnung.
    assert view["display_grid_fee_rp"] == "8.00"
    assert Decimal(charged["unit_price_chf_per_kwh"]) == Decimal(
        INVOICE_ROW["policy_snapshot"]["internal_price_chf_per_kwh"]
    )


def test_invoice_savings_view_returns_none_for_missing_or_foreign_invoice(
    monkeypatch,
):
    """Missing id and another participant's id fail the same owner-scoped
    query and stay indistinguishable; the member's readings are never read."""
    import member_savings

    lookup = MagicMock(return_value=None)
    fetch_readings = MagicMock()
    monkeypatch.setattr(
        member_savings.db, "get_invoice_for_participant", lookup
    )
    monkeypatch.setattr(
        member_savings.db, "get_building_period_readings", fetch_readings
    )

    assert member_savings.invoice_savings_view(999, "building-session") is None

    fetch_readings.assert_not_called()


def test_invoice_savings_view_wraps_readings_store_failure_closed(monkeypatch):
    import member_savings

    monkeypatch.setattr(
        member_savings.db,
        "get_invoice_for_participant",
        MagicMock(return_value=dict(INVOICE_ROW)),
    )
    monkeypatch.setattr(
        member_savings.db,
        "get_building_period_readings",
        MagicMock(side_effect=RuntimeError("db down")),
    )

    with pytest.raises(member_savings.MemberSavingsDataError):
        member_savings.invoice_savings_view(42, "building-session")


def test_invoice_savings_view_rejects_a_row_of_another_participant(monkeypatch):
    import member_savings

    row = dict(INVOICE_ROW, participant_id="building-other")
    monkeypatch.setattr(
        member_savings.db,
        "get_invoice_for_participant",
        MagicMock(return_value=row),
    )
    monkeypatch.setattr(
        member_savings.db, "get_building_period_readings", MagicMock()
    )

    with pytest.raises(member_savings.MemberSavingsDataError):
        member_savings.invoice_savings_view(42, "building-session")


# === Route wiring (dashboard_routes.py, full Flask app) ===


def _patch_savings(flask_app_module, monkeypatch, view):
    monkeypatch.setattr(
        flask_app_module.dashboard_module,
        "member_invoice_savings_view",
        MagicMock(return_value=view),
    )


def _detail_view():
    return {
        "id": 42,
        "invoice_number": "LEG-2026-000001",
        "issuer_name": "LEG Musterweg",
        "period_label": "Juli 2026",
        "issue_date": "2026-08-05",
        "due_date": "2026-09-04",
        "vat_mode_label": "Keine Mehrwertsteuer",
        "display_vat_rate_pct": "0.00",
        "display_policy_unit_price_rp": "15.00",
        "display_grid_fee_rp": "8.00",
        "policy_payment_days": 30,
        "display_net_chf": "0.15",
        "display_vat_chf": "0.00",
        "display_gross_chf": "0.15",
        "charges": [],
        "credits": [],
        "rounding_adjustments": [],
    }


def test_dashboard_invoice_detail_renders_realized_savings(
    dashboard_app_module,  # noqa: F811
    monkeypatch,
):
    monkeypatch.setattr(
        dashboard_app_module.dashboard_module,
        "member_invoice_detail",
        MagicMock(return_value=_detail_view()),
    )
    savings = {
        "available": True,
        "period_label": "Juli 2026",
        "display_consumption_kwh": "2.000",
        "display_local_kwh": "1.000",
        "display_local_share_pct": "50.0",
        "display_savings_chf": "0.03",
        "display_grid_fee_rp": "8.00",
        "network_level_label": "Gleiche Netzebene",
    }
    _patch_savings(dashboard_app_module, monkeypatch, savings)
    client = dashboard_app_module.web.test_client()
    _set_session(client)

    response = client.get("/dashboard/invoices/42")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    body = response.get_data(as_text=True)
    assert "Realisierte Ersparnis" in body
    assert "2.000 kWh" in body
    assert "50.0 %" in body
    assert "0.03 CHF" in body
    assert "8.00 Rp./kWh" in body


def test_dashboard_invoice_detail_renders_empty_state_when_readings_incomplete(
    dashboard_app_module,  # noqa: F811
    monkeypatch,
):
    import member_savings

    monkeypatch.setattr(
        dashboard_app_module.dashboard_module,
        "member_invoice_detail",
        MagicMock(return_value=_detail_view()),
    )
    _patch_savings(
        dashboard_app_module,
        monkeypatch,
        {
            "available": False,
            "period_label": "Juli 2026",
            "message": member_savings.EMPTY_STATE_MESSAGE,
        },
    )
    client = dashboard_app_module.web.test_client()
    _set_session(client)

    response = client.get("/dashboard/invoices/42")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "keine vollständigen Messwerte" in body
    assert "2.000 kWh" not in body


def test_dashboard_invoice_detail_corrupted_savings_data_is_503(
    dashboard_app_module,  # noqa: F811
    monkeypatch,
):
    import member_savings

    monkeypatch.setattr(
        dashboard_app_module.dashboard_module,
        "member_invoice_detail",
        MagicMock(return_value=_detail_view()),
    )
    monkeypatch.setattr(
        dashboard_app_module.dashboard_module,
        "member_invoice_savings_view",
        MagicMock(side_effect=member_savings.MemberSavingsDataError("bad data")),
    )
    client = dashboard_app_module.web.test_client()
    _set_session(client)

    response = client.get("/dashboard/invoices/42")

    assert response.status_code == 503
    assert response.headers["Cache-Control"] == "no-store"


def test_dashboard_invoice_detail_cross_member_never_reads_metered_numbers(
    dashboard_app_module,  # noqa: F811
    monkeypatch,
):
    """A foreign invoice id is a plain 404 and the member's own readings are
    never loaded for it."""
    monkeypatch.setattr(
        dashboard_app_module.dashboard_module,
        "member_invoice_detail",
        MagicMock(return_value=None),
    )
    savings_view = MagicMock()
    monkeypatch.setattr(
        dashboard_app_module.dashboard_module,
        "member_invoice_savings_view",
        savings_view,
    )
    client = dashboard_app_module.web.test_client()
    _set_session(client, building_id="building-attacker")

    response = client.get("/dashboard/invoices/42")

    assert response.status_code == 404
    savings_view.assert_not_called()


def test_dashboard_invoice_detail_requires_session(dashboard_app_module):  # noqa: F811
    client = dashboard_app_module.web.test_client()
    response = client.get("/dashboard/invoices/42")
    assert response.status_code == 401


# === Template contract ===

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def test_member_invoice_detail_template_has_accessible_savings_section():
    text = (TEMPLATES_DIR / "member_invoice_detail.html").read_text(encoding="utf-8")
    assert 'aria-labelledby="savings-title"' in text
    assert 'id="savings-title"' in text
    assert "tabular-nums" in text
