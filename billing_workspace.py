# SPDX-License-Identifier: AGPL-3.0-or-later
"""Display-ready audit model for persisted billing drafts."""

import json
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

import database as db

_MONTHS = (
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
)


class BillingPeriodNotFound(LookupError):
    """The requested billing draft does not exist."""


def _normalise(value):
    """Convert temporal containers while preserving exact decimal values."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _normalise(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalise(item) for item in value]
    return value


def _decimal_text(value, places, scale=Decimal(1)):
    """Format a decimal with the billing engine's half-up policy."""
    try:
        decimal_value = Decimal(str(value)) * scale
        if not decimal_value.is_finite():
            return format(Decimal(0), f".{places}f")
        quantum = Decimal(1).scaleb(-places)
        return format(decimal_value.quantize(quantum, rounding=ROUND_HALF_UP), "f")
    except (ArithmeticError, TypeError, ValueError):
        return format(Decimal(0), f".{places}f")


def _display_line_item(item):
    """Add exact, preformatted display fields to a persisted line item."""
    result = _normalise(item)
    result["display_quantity_kwh"] = (
        _decimal_text(item.get("quantity_kwh"), 3)
        if item.get("quantity_kwh") is not None
        else None
    )
    result["display_unit_price_rp"] = (
        _decimal_text(item.get("unit_price_chf_per_kwh"), 2, Decimal(100))
        if item.get("unit_price_chf_per_kwh") is not None
        else None
    )
    result["display_amount_chf"] = _decimal_text(
        item.get("amount_chf", 0),
        6 if item.get("item_type") == "rounding_adjustment" else 2,
    )
    return result


def _json_value(value, fallback):
    """Decode persisted JSON while falling back on malformed values."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return fallback
    return value if isinstance(value, type(fallback)) else fallback


def _period_label(value):
    """Return a Swiss High German month and year label."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    if isinstance(value, (datetime, date)):
        return f"{_MONTHS[value.month - 1]} {value.year}"
    return "Periode"


def _period_summary(period):
    """Build the compact period data used by the selector."""
    result = _normalise(period)
    result["period_label"] = _period_label(period.get("period_start"))
    result["status_label"] = (
        "Entwurf" if period.get("status") == "draft" else str(period.get("status", ""))
    )
    return result


def _is_balanced(reconciliation):
    """Return whether every available reconciliation difference is zero."""
    if not reconciliation:
        return False
    differences = []

    def collect(value, key=""):
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                collect(child_value, child_key)
        elif key.endswith("difference_kwh"):
            try:
                differences.append(Decimal(str(value)))
            except (ArithmeticError, TypeError, ValueError):
                differences.append(Decimal("NaN"))

    collect(reconciliation)
    return bool(differences) and all(
        value.is_finite() and value == 0 for value in differences
    )


def _detail_model(period):
    """Build the complete read-only audit model for one billing period."""
    raw_reconciliation = _json_value(period.get("reconciliation"), {})
    source_ids = _json_value(period.get("source_document_ids"), [])
    line_items = [_display_line_item(item) for item in period.get("line_items", [])]
    result = _period_summary(period)
    result.update(
        draft_notice=(
            "Dieser Abrechnungsentwurf ist keine definitive Rechnung. "
            "Prüfen Sie Tarif, Abgleich und Quelldokumente."
        ),
        metrics={
            "production_kwh": _normalise(period.get("total_production_kwh", 0)),
            "allocated_kwh": _normalise(period.get("total_allocated_kwh", 0)),
            "surplus_kwh": _normalise(period.get("total_surplus_kwh", 0)),
            "network_discount_chf": _normalise(
                period.get("total_network_discount_chf", 0)
            ),
        },
        metrics_display={
            "production_kwh": _decimal_text(period.get("total_production_kwh", 0), 2),
            "allocated_kwh": _decimal_text(period.get("total_allocated_kwh", 0), 2),
            "surplus_kwh": _decimal_text(period.get("total_surplus_kwh", 0), 2),
            "network_discount_chf": _decimal_text(
                period.get("total_network_discount_chf", 0), 2
            ),
        },
        tariff={
            "internal_price": _rate(period.get("internal_price_chf_per_kwh")),
            "grid_fee": _rate(period.get("grid_fee_chf_per_kwh")),
            "distribution_model": {
                "proportional": "Proportional",
                "einfach": "Einfach",
                "simple": "Einfach",
            }.get(
                period.get("distribution_model"),
                str(period.get("distribution_model") or "Nicht angegeben"),
            ),
            "network_level": {
                "same": "Gleiche Netzebene",
                "different": "Unterschiedliche Netzebenen",
            }.get(
                period.get("network_level"),
                str(period.get("network_level") or "Nicht angegeben"),
            ),
        },
        reconciliation={
            **_normalise(raw_reconciliation),
            "balanced": _is_balanced(raw_reconciliation),
            "label": (
                "Vollständig abgeglichen"
                if _is_balanced(raw_reconciliation)
                else "Abweichung prüfen"
            ),
        },
        provenance={
            "source_document_ids": _normalise(source_ids),
            "source_count": len(source_ids),
            "input_fingerprint": period.get("input_fingerprint") or "",
        },
        consumer_charges=[
            item for item in line_items if item.get("item_type") == "consumer_charge"
        ],
        producer_credits=[
            item for item in line_items if item.get("item_type") == "producer_credit"
        ],
        rounding_adjustments=[
            item
            for item in line_items
            if item.get("item_type") == "rounding_adjustment"
        ],
    )
    return result


def _rate(value):
    """Format a CHF/kWh decimal as Rp./kWh for display."""
    if value is None:
        return "Nicht angegeben"
    try:
        rate_rp = Decimal(str(value)) * 100
        if not rate_rp.is_finite():
            return "Nicht angegeben"
        rounded = rate_rp.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return f"{rounded:.2f} Rp./kWh"
    except (ArithmeticError, TypeError, ValueError):
        return "Nicht angegeben"


def load(period_id=None):
    """Load all draft summaries and one display-ready selected period."""
    periods = db.list_billing_periods(limit=100)
    if not periods:
        if period_id is not None:
            raise BillingPeriodNotFound(period_id)
        return {"empty": True, "periods": [], "selected": None}

    selected_id = int(period_id) if period_id is not None else periods[0]["id"]
    selected = db.get_billing_period(selected_id)
    if not selected:
        raise BillingPeriodNotFound(selected_id)
    return {
        "empty": False,
        "periods": [_period_summary(period) for period in periods],
        "selected": _detail_model(selected),
    }
