# SPDX-License-Identifier: AGPL-3.0-or-later
"""Quartierakku: one shared storage battery per LEG community.

Winterthur case: one 45 kWh battery for eight households, roughly CHF 120
per year and household, one person buys it and the rest pay their share.
Design for one battery per community until a second case shows up. The
asset carries capacity in kWh, an annual cost in CHF, and one cost share
per participant. Energy allocation through the battery arrives separately;
this module owns the asset and its cost split only.

Every invalid choice is refused with a German diagnostic: OpenLEG never
guesses money-path inputs.
"""

import re
from calendar import isleap
from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

MAX_CAPACITY_KWH = Decimal(10000)
MAX_ANNUAL_COST_CHF = Decimal(100000)
SHARE_MIN_PCT = Decimal(0)
SHARE_MAX_PCT = Decimal(100)

_CAPTACITY_PREFIX = "share:"

_CAPACITY_ERROR = (
    "Speicherkapazität in kWh, grösser 0 und höchstens 10000, höchstens "
    "2 Nachkommastellen."
)
_COST_ERROR = (
    "Jahreskosten in CHF, zwischen 0 und 100000, höchstens 2 Nachkommastellen."
)
_SHARE_ERROR = (
    "Anteil für {participant} in Prozent, zwischen 0 und 100, höchstens "
    "2 Nachkommastellen."
)
_SHARE_SUM_ERROR = "Die Anteile müssen zusammen 100 Prozent ergeben."
_SHARE_UNKNOWN_ERROR = "Die Anteile enthalten unbekannte Teilnehmer."
_SHARE_MISSING_ERROR = "Jeder Teilnehmer braucht einen Anteil am Quartierakku."

_PLAIN_DECIMAL_PATTERN = re.compile(r"^\d+(\.\d+)?$")
_KWH_QUANTUM = Decimal("0.000001")


class InvalidBatteryConfig(ValueError):
    """A battery record is incomplete, out of bounds, or malformed."""


def _decimal(value):
    try:
        return Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError, InvalidOperation):
        return Decimal("NaN")


def _stored_money(value, message):
    """Parse one stored money value: finite, at most 2 decimals."""
    number = _decimal(value)
    if not number.is_finite() or -number.as_tuple().exponent > 2:
        raise InvalidBatteryConfig(message)
    return number


def _within_places(value, places=2):
    return value.is_finite() and -value.as_tuple().exponent <= places


def validate_battery_config(config) -> dict:
    """Validate and normalize one stored battery config, failing closed."""
    if not isinstance(config, dict):
        raise InvalidBatteryConfig("Der Quartierakku hat keine gültigen Angaben.")
    capacity = _stored_money(config.get("capacity_kwh"), _CAPACITY_ERROR)
    if capacity <= 0 or capacity > MAX_CAPACITY_KWH:
        raise InvalidBatteryConfig(_CAPACITY_ERROR)
    annual_cost = _stored_money(config.get("annual_cost_chf"), _COST_ERROR)
    if annual_cost < 0 or annual_cost > MAX_ANNUAL_COST_CHF:
        raise InvalidBatteryConfig(_COST_ERROR)

    shares = config.get("shares")
    if not isinstance(shares, dict) or not shares:
        raise InvalidBatteryConfig(_SHARE_MISSING_ERROR)
    normalized_shares = {}
    for participant, share in shares.items():
        if not isinstance(participant, str) or not participant.strip():
            raise InvalidBatteryConfig(
                "Jeder Anteil braucht eine gültige Teilnehmer-ID."
            )
        share_value = _decimal(share)
        if (
            not share_value.is_finite()
            or not _within_places(share_value)
            or share_value < SHARE_MIN_PCT
            or share_value > SHARE_MAX_PCT
        ):
            raise InvalidBatteryConfig(_SHARE_ERROR.format(participant=participant))
        normalized_shares[participant] = share_value
    total = sum(normalized_shares.values(), Decimal(0))
    if total != SHARE_MAX_PCT:
        raise InvalidBatteryConfig(_SHARE_SUM_ERROR)
    return {
        "community_id": config.get("community_id"),
        "capacity_kwh": capacity,
        "annual_cost_chf": annual_cost,
        "shares": normalized_shares,
    }


def _form_value(form, name):
    value = form.get(name, "")
    if not isinstance(value, str):
        return ""
    return value.strip()


def validate_battery_form(form, member_ids) -> dict:
    """Validate one Quartierakku form submission.

    Returns ``{"battery": {...}, "errors": {field: message}}`` on success and
    ``{"battery": None, "errors": {...}}`` otherwise. No field is guessed; a
    member without an input pays no share.
    """
    errors = {}

    capacity_text = _form_value(form, "capacity_kwh")
    capacity = None
    if capacity_text and _PLAIN_DECIMAL_PATTERN.match(capacity_text):
        candidate = _decimal(capacity_text)
        if _within_places(candidate) and 0 < candidate <= MAX_CAPACITY_KWH:
            capacity = candidate
    if capacity is None:
        errors["capacity_kwh"] = _CAPACITY_ERROR

    cost_text = _form_value(form, "annual_cost_chf")
    annual_cost = None
    if cost_text and _PLAIN_DECIMAL_PATTERN.match(cost_text):
        candidate = _decimal(cost_text)
        if _within_places(candidate) and 0 <= candidate <= MAX_ANNUAL_COST_CHF:
            annual_cost = candidate
    if annual_cost is None:
        errors["annual_cost_chf"] = _COST_ERROR

    shares = {}
    member_set = {member for member in member_ids if isinstance(member, str) and member}
    for name in form:
        if not isinstance(name, str) or not name.startswith(_CAPTACITY_PREFIX):
            continue
        participant = name[len(_CAPTACITY_PREFIX) :]
        if participant not in member_set:
            errors["shares"] = _SHARE_UNKNOWN_ERROR
            continue
        text = _form_value(form, name)
        share = None
        if text:
            if _PLAIN_DECIMAL_PATTERN.match(text):
                candidate = _decimal(text)
                if (
                    _within_places(candidate)
                    and SHARE_MIN_PCT <= candidate <= SHARE_MAX_PCT
                ):
                    share = candidate
        else:
            share = Decimal(0)
        if share is None:
            errors[name] = _SHARE_ERROR.format(participant=participant)
            continue
        shares[participant] = share

    if not errors:
        total = sum(shares.values(), Decimal(0))
        if total != SHARE_MAX_PCT:
            errors["shares"] = _SHARE_SUM_ERROR

    if errors:
        return {"battery": None, "errors": errors}
    return {
        "battery": {
            "capacity_kwh": capacity,
            "annual_cost_chf": annual_cost,
            "shares": shares,
        },
        "errors": {},
    }


def cost_share_chf(annual_cost_chf, share_pct) -> Decimal:
    """One participant's cost-share amount in CHF at persisted precision."""
    return (_decimal(annual_cost_chf) * _decimal(share_pct) / Decimal(100)).quantize(
        _KWH_QUANTUM, rounding=ROUND_HALF_UP
    )


def period_fraction(period_start, period_end) -> Decimal:
    """Return the exact annual fraction for a half-open billing window."""

    def as_datetime(value):
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value)
            except ValueError as exc:
                raise InvalidBatteryConfig(
                    "Die Abrechnungsperiode ist ungültig."
                ) from exc
        if isinstance(value, datetime):
            return value
        if isinstance(value, date):
            return datetime.combine(value, time.min)
        raise InvalidBatteryConfig("Die Abrechnungsperiode ist ungültig.")

    start = as_datetime(period_start)
    end = as_datetime(period_end)
    if end <= start:
        raise InvalidBatteryConfig("Die Abrechnungsperiode ist ungültig.")
    fraction = Decimal(0)
    cursor = start
    while cursor < end:
        year_end = datetime(cursor.year + 1, 1, 1, tzinfo=cursor.tzinfo)
        segment_end = min(end, year_end)
        seconds = Decimal(str((segment_end - cursor).total_seconds()))
        year_seconds = Decimal(366 if isleap(cursor.year) else 365) * Decimal(86400)
        fraction += seconds / year_seconds
        cursor = segment_end
    return fraction


def period_cost_share_chf(
    annual_cost_chf, share_pct, period_start, period_end
) -> Decimal:
    """Prorate one annual participant share over a billing window."""
    return (
        _decimal(annual_cost_chf)
        * _decimal(share_pct)
        / Decimal(100)
        * period_fraction(period_start, period_end)
    ).quantize(_KWH_QUANTUM, rounding=ROUND_HALF_UP)
