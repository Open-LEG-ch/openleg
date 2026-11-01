# SPDX-License-Identifier: AGPL-3.0-or-later
"""Display-ready audit model for persisted billing drafts."""

import json
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

import database as db
from translation_markers import gettext_noop as _N

# Perioden sind halboffene Zürcher Kalendermonate, gespeichert in UTC.
PERIOD_TIMEZONE = "Europe/Zurich"

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


_LOCAL_TZ = ZoneInfo(PERIOD_TIMEZONE)


def _period_label(value):
    """Return a Swiss High German month and year label.

    Period boundaries are stored in UTC, so an August period starts at
    2026-07-31T22:00Z. Reading the month off that without converting back to
    the period timezone labels the August draft "Juli 2026".
    """
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    if isinstance(value, datetime) and value.tzinfo is not None:
        # Periods are Europe/Zurich calendar months stored as UTC instants:
        # September starts at 2026-08-31T22:00Z and must not read as August.
        value = value.astimezone(_LOCAL_TZ)
    if isinstance(value, (datetime, date)):
        return f"{_MONTHS[value.month - 1]} {value.year}"
    return _N("Periode")


def period_label(value):
    """Public seam: Swiss High German month and year label for a period start."""
    return _period_label(value)


def readiness_flags(period):
    """Public seam: reconciliation balance and source count of one period.

    Tolerates the JSONB representations produced by PostgreSQL (dicts/lists)
    as well as raw JSON strings.
    """
    reconciliation = _json_value(period.get("reconciliation"), {})
    source_ids = _json_value(period.get("source_document_ids"), [])
    return {
        "reconciled": _is_balanced(reconciliation),
        "exact_source": _is_exact_source(reconciliation),
        "source_count": len(source_ids),
    }


def _period_summary(period):
    """Build the compact period data used by the selector."""
    result = _normalise(period)
    result["period_label"] = _period_label(period.get("period_start"))
    result["status_label"] = (
        _N("Entwurf")
        if period.get("status") == "draft"
        else _N("Entwurf ungültig")
        if period.get("status") == "invalidated"
        else str(period.get("status", ""))
    )
    return result


def _is_exact_source(reconciliation):
    """Whether the draft bills the exact VNB source rather than the engine."""
    return isinstance(reconciliation, dict) and isinstance(
        reconciliation.get("precision_policy"), dict
    )


def _is_balanced(reconciliation):
    """Return whether the draft's reconciliation leaves nothing to explain.

    Usually every difference must be zero. Exact-source drafts bill the VNB
    quantities, so their differences to the engine's own allocation are
    expected and only need to be readable; approval re-derives the billed
    quantities from the source itself.
    """
    if not reconciliation:
        return False
    if any(
        reconciliation.get(key) is not None
        for key in ("vnb_imbalance_release", "vnb_imbalance_tolerance_ratio")
    ):
        return False
    for key in ("vnb_imbalance_kwh", "vnb_imbalance_ratio"):
        if key in reconciliation:
            try:
                value = Decimal(str(reconciliation[key]))
            except (ArithmeticError, TypeError, ValueError):
                return False
            if not value.is_finite() or value != 0:
                return False
    exact_source = _is_exact_source(reconciliation)
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
        value.is_finite() and (exact_source or value == 0) for value in differences
    )


def _detail_model(period):
    """Build the complete read-only audit model for one billing period."""
    raw_reconciliation = _json_value(period.get("reconciliation"), {})
    balanced = _is_balanced(raw_reconciliation)
    source_ids = _json_value(period.get("source_document_ids"), [])
    battery_snapshot = _json_value(period.get("battery_snapshot"), {})
    raw_battery_energy = _json_value(battery_snapshot.get("energy"), {})
    raw_attribution = _json_value(battery_snapshot.get("attribution"), {})
    battery_energy = (
        {
            key: _decimal_text(raw_battery_energy.get(key), 3)
            for key in (
                "charged_kwh",
                "discharged_kwh",
                "charge_discharge_difference_kwh",
                "allocated_battery_kwh",
                "unallocated_discharge_kwh",
            )
        }
        if raw_battery_energy
        else None
    )
    battery_attribution = [
        {
            "participant_id": participant_id,
            "direct_solar_kwh": _decimal_text(values.get("direct_solar_kwh"), 3),
            "battery_kwh": _decimal_text(values.get("battery_kwh"), 3),
            "value_chf": _decimal_text(values.get("value_chf"), 2),
        }
        for participant_id, values in sorted(raw_attribution.items())
        if isinstance(participant_id, str) and isinstance(values, dict)
    ]
    line_items = [_display_line_item(item) for item in period.get("line_items", [])]
    result = _period_summary(period)
    result.update(
        draft_notice=_N(
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
                "proportional": _N("Proportional"),
                "einfach": _N("Einfach"),
                "simple": _N("Einfach"),
            }.get(
                period.get("distribution_model"),
                str(period.get("distribution_model") or _N("Nicht angegeben")),
            ),
            "network_level": {
                "same": _N("Gleiche Netzebene"),
                "different": _N("Unterschiedliche Netzebenen"),
            }.get(
                period.get("network_level"),
                str(period.get("network_level") or _N("Nicht angegeben")),
            ),
        },
        reconciliation={
            **_normalise(raw_reconciliation),
            "balanced": balanced,
            "label": (
                _N("Abweichung prüfen")
                if not balanced
                else _N(
                    "Exakte VNB-Quelle massgebend, Abweichung zum Modell ausgewiesen"
                )
                if _is_exact_source(raw_reconciliation)
                else _N("Vollständig abgeglichen")
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
        settlement_charges=[
            item for item in line_items if item.get("item_type") == "settlement_fee"
        ],
        battery_shares=[
            item for item in line_items if item.get("item_type") == "battery_cost_share"
        ],
        battery_energy=battery_energy,
        battery_attribution=battery_attribution,
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
        return _N("Nicht angegeben")
    try:
        rate_rp = Decimal(str(value)) * 100
        if not rate_rp.is_finite():
            return _N("Nicht angegeben")
        rounded = rate_rp.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return f"{rounded:.2f} Rp./kWh"
    except (ArithmeticError, TypeError, ValueError):
        return _N("Nicht angegeben")


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
