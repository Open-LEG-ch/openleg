# SPDX-License-Identifier: AGPL-3.0-or-later
"""Validation for the versioned LEG billing policy.

Domain logic only, no SQL. The admin form submits Rappen per kWh; the policy
stores CHF per kWh (``internal_price_chf_per_kwh``), matching billing_engine
and the ``billing_tariffs`` table. Every invalid choice is refused: OpenLEG
never guesses money-path inputs.
"""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

NETWORK_LEVELS = ("same", "cross")
DISTRIBUTION_MODELS = ("proportional", "einfach")
VAT_MODES = ("none", "standard")
DELIVERY_METHODS = ("email", "download")

MIN_PAYMENT_DAYS = 1
MAX_PAYMENT_DAYS = 365
MAX_VAT_RATE_PCT = Decimal(100)
MAX_PRICE_RP = Decimal(1000)
MAX_PRICE_DECIMALS_RP = 4
INVOICE_PREFIX_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9-]{1,15}\Z")
_STRICT_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PLAIN_DECIMAL_PATTERN = re.compile(r"^\d+(\.\d+)?$")

POLICY_DISCLAIMER = (
    "Tarife und Mehrwertsteuer liegen in der Verantwortung der LEG. "
    "OpenLEG erteilt keine Rechts- oder Steuerberatung und legt diese Werte "
    "nicht fest. Prüfen Sie Ihre Wahl vor dem Speichern."
)

PERSISTED_POLICY_FIELDS = (
    "tariff_id",
    "community_id",
    "effective_from",
    "internal_price_chf_per_kwh",
    "grid_fee_chf_per_kwh",
    "network_level",
    "distribution_model",
    "vat_mode",
    "vat_rate_pct",
    "payment_days",
    "invoice_prefix",
    "delivery_method",
)

EDITABLE_POLICY_FIELDS = PERSISTED_POLICY_FIELDS[2:]

_FORM_INPUT_FOR_POLICY_FIELD = {
    "internal_price_chf_per_kwh": "internal_price_rp",
    "grid_fee_chf_per_kwh": "grid_fee_rp",
}

FORM_FIELDS = tuple(
    _FORM_INPUT_FOR_POLICY_FIELD.get(field, field) for field in EDITABLE_POLICY_FIELDS
)
FINGERPRINT_POLICY_FIELDS = tuple(
    field for field in PERSISTED_POLICY_FIELDS if field != "effective_from"
)
_DECIMAL_POLICY_FIELDS = (
    "internal_price_chf_per_kwh",
    "grid_fee_chf_per_kwh",
    "vat_rate_pct",
)

NETWORK_LEVEL_LABELS = {
    "same": "Gleiche Netzebene",
    "cross": "Unterschiedliche Netzebenen",
}
DISTRIBUTION_MODEL_LABELS = {
    "proportional": "Proportional",
    "einfach": "Einfach",
}
VAT_MODE_LABELS = {
    "none": "Keine Mehrwertsteuer",
    "standard": "Mehrwertsteuer ausweisen",
}
DELIVERY_METHOD_LABELS = {
    "email": "E-Mail",
    "download": "PDF-Download",
}


class InvalidPersistedPolicy(ValueError):
    """A stored billing policy is incomplete or outside its value domain."""


def _persisted_decimal(value):
    try:
        return Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        return Decimal("NaN")


def _persisted_temporal(value):
    if isinstance(value, (datetime, date)):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            try:
                return date.fromisoformat(value)
            except ValueError:
                pass
    raise InvalidPersistedPolicy(
        "Das Inkrafttretungsdatum der Richtlinie ist ungültig."
    )


def _has_precision(value, places):
    return value.is_finite() and -value.as_tuple().exponent <= places


def validate_persisted_policy(policy, *, period_start, community_id) -> dict:
    """Validate and normalize one stored policy snapshot, failing closed."""
    if not isinstance(policy, dict) or not policy:
        raise InvalidPersistedPolicy(
            "Der Abrechnungsentwurf hat keine Richtlinien-Kopie."
        )
    missing = [field for field in PERSISTED_POLICY_FIELDS if policy.get(field) is None]
    if missing:
        raise InvalidPersistedPolicy(
            "Die Richtlinien-Kopie ist unvollständig: " + ", ".join(missing)
        )
    if policy["community_id"] != community_id or not isinstance(
        policy["community_id"], str
    ):
        raise InvalidPersistedPolicy(
            "Die Richtlinien-Kopie gehört nicht zur Community des Entwurfs."
        )
    tariff_id = policy["tariff_id"]
    if isinstance(tariff_id, bool) or not isinstance(tariff_id, int) or tariff_id <= 0:
        raise InvalidPersistedPolicy("Die Tarif-ID der Richtlinie ist ungültig.")

    normalized = dict(policy)
    max_price_chf = MAX_PRICE_RP / Decimal(100)
    for field in _DECIMAL_POLICY_FIELDS[:2]:
        value = _persisted_decimal(policy[field])
        if not _has_precision(value, 6) or value < 0 or value > max_price_chf:
            raise InvalidPersistedPolicy(
                "Ein Energiepreis der Richtlinie liegt ausserhalb des zulässigen Bereichs."
            )
        normalized[field] = value

    effective_from = _persisted_temporal(policy["effective_from"])
    try:
        effective = effective_from <= period_start
    except TypeError:
        effective = False
    if not effective:
        raise InvalidPersistedPolicy(
            "Die Richtlinie gilt noch nicht zum Periodenbeginn."
        )

    for field, allowed, message in (
        ("network_level", NETWORK_LEVELS, "Die Netzebene der Richtlinie ist ungültig."),
        (
            "distribution_model",
            DISTRIBUTION_MODELS,
            "Das Verteilmodell der Richtlinie ist ungültig.",
        ),
        (
            "delivery_method",
            DELIVERY_METHODS,
            "Die Zustellmethode der Richtlinie ist ungültig.",
        ),
    ):
        if policy[field] not in allowed:
            raise InvalidPersistedPolicy(message)

    if not isinstance(
        policy["invoice_prefix"], str
    ) or not INVOICE_PREFIX_PATTERN.match(policy["invoice_prefix"]):
        raise InvalidPersistedPolicy("Das Rechnungspräfix der Richtlinie ist ungültig.")
    payment_days = policy["payment_days"]
    if (
        isinstance(payment_days, bool)
        or not isinstance(payment_days, int)
        or not MIN_PAYMENT_DAYS <= payment_days <= MAX_PAYMENT_DAYS
    ):
        raise InvalidPersistedPolicy("Die Zahlungsfrist der Richtlinie ist ungültig.")

    vat_mode = policy["vat_mode"]
    vat_rate = _persisted_decimal(policy["vat_rate_pct"])
    if vat_mode not in VAT_MODES or not _has_precision(vat_rate, 2):
        raise InvalidPersistedPolicy(
            "Der Mehrwertsteuersatz der Richtlinie ist ungültig."
        )
    if vat_mode == "none" and vat_rate != 0:
        raise InvalidPersistedPolicy("Ohne Mehrwertsteuer muss der Satz 0 sein.")
    if vat_mode == "standard" and not 0 < vat_rate <= MAX_VAT_RATE_PCT:
        raise InvalidPersistedPolicy(
            "Der Mehrwertsteuersatz der Richtlinie ist ungültig."
        )
    normalized["vat_rate_pct"] = vat_rate
    return normalized


def policy_fingerprint_values(policy: dict) -> dict:
    """Project the complete persisted policy into deterministic JSON values."""
    projected = {}
    for field in FINGERPRINT_POLICY_FIELDS:
        value = policy[field]
        if field in _DECIMAL_POLICY_FIELDS:
            value = str(value)
        elif isinstance(value, (datetime, date)):
            value = value.isoformat()
        projected[field] = value
    return projected


def rate_rp_text(value):
    """Format a stored CHF/kWh price as Rp./kWh for display."""
    try:
        rate_rp = Decimal(str(value)) * 100
    except (ArithmeticError, InvalidOperation):
        return "Nicht angegeben"
    if not rate_rp.is_finite():
        return "Nicht angegeben"
    return f"{rate_rp:.2f} Rp./kWh"


def describe_version(version: dict) -> dict:
    """Return a display-ready copy of one persisted policy version."""
    described = dict(version)
    effective = version.get("effective_from")
    described["effective_from_display"] = (
        effective.isoformat()[:10]
        if hasattr(effective, "isoformat")
        else str(effective)
    )
    described["internal_price_display"] = rate_rp_text(
        version.get("internal_price_chf_per_kwh")
    )
    described["grid_fee_display"] = rate_rp_text(version.get("grid_fee_chf_per_kwh"))
    described["network_level_label"] = NETWORK_LEVEL_LABELS.get(
        version.get("network_level"), "Nicht angegeben"
    )
    described["distribution_model_label"] = DISTRIBUTION_MODEL_LABELS.get(
        version.get("distribution_model"), "Nicht angegeben"
    )
    described["vat_label"] = VAT_MODE_LABELS.get(
        version.get("vat_mode"), "Nicht angegeben"
    )
    described["delivery_method_label"] = DELIVERY_METHOD_LABELS.get(
        version.get("delivery_method"), "Nicht angegeben"
    )
    payment_days = version.get("payment_days")
    described["payment_days_display"] = (
        f"{payment_days} Tage" if payment_days is not None else "Nicht angegeben"
    )
    invoice_prefix = version.get("invoice_prefix")
    described["invoice_prefix_display"] = (
        invoice_prefix if invoice_prefix is not None else "Nicht angegeben"
    )
    return described


def _field(form, name):
    value = form.get(name, "")
    if not isinstance(value, str):
        return ""
    return value.strip()


def _price_rp(value):
    """Parse a Rp./kWh price; refuse negative, non-finite and overprecise."""
    if not value or not _PLAIN_DECIMAL_PATTERN.match(value):
        return None
    try:
        price = Decimal(value)
    except InvalidOperation:
        return None
    if not price.is_finite() or price > MAX_PRICE_RP:
        return None
    if -price.as_tuple().exponent > MAX_PRICE_DECIMALS_RP:
        return None
    return price


def _effective_from(value):
    if not _STRICT_DATE_PATTERN.match(value):
        return None
    try:
        d = date.fromisoformat(value)
    except ValueError:
        return None
    return datetime(d.year, d.month, d.day, tzinfo=ZoneInfo("Europe/Zurich"))


def _payment_days(value):
    if not value.isdigit():
        return None
    days = int(value)
    if not MIN_PAYMENT_DAYS <= days <= MAX_PAYMENT_DAYS:
        return None
    return days


def _vat_rate(value):
    if not value or not _PLAIN_DECIMAL_PATTERN.match(value):
        return None
    try:
        rate = Decimal(value)
    except InvalidOperation:
        return None
    if not rate.is_finite() or rate <= 0 or rate > MAX_VAT_RATE_PCT:
        return None
    if -rate.as_tuple().exponent > 2:
        return None
    return rate


def _enum(value, allowed):
    return value if value in allowed else None


def validate_policy_form(form) -> dict:
    """Validate one billing policy form submission.

    Returns ``{"policy": {...}, "errors": {}}`` on success, otherwise
    ``{"policy": None, "errors": {field: message}}``. No field is guessed.
    """
    errors = {}

    effective_from = _effective_from(_field(form, "effective_from"))
    if effective_from is None:
        errors["effective_from"] = "Gültig-ab-Datum im Format JJJJ-MM-TT eingeben."

    internal_price_rp = _price_rp(_field(form, "internal_price_rp"))
    if internal_price_rp is None:
        errors["internal_price_rp"] = (
            "Interner Preis in Rp./kWh, zwischen 0 und 1000, höchstens 4 Nachkommastellen."
        )
    grid_fee_rp = _price_rp(_field(form, "grid_fee_rp"))
    if grid_fee_rp is None:
        errors["grid_fee_rp"] = (
            "Netzentgelt in Rp./kWh, zwischen 0 und 1000, höchstens 4 Nachkommastellen."
        )

    network_level = _enum(_field(form, "network_level"), NETWORK_LEVELS)
    if network_level is None:
        errors["network_level"] = "Unbekannte Netzebene."
    distribution_model = _enum(_field(form, "distribution_model"), DISTRIBUTION_MODELS)
    if distribution_model is None:
        errors["distribution_model"] = "Unbekanntes Verteilmodell."

    vat_mode = _enum(_field(form, "vat_mode"), VAT_MODES)
    if vat_mode is None:
        errors["vat_mode"] = "Unbekannter Mehrwertsteuer-Modus."
    vat_rate_input = _field(form, "vat_rate_pct")
    vat_rate_pct = Decimal(0)
    if vat_mode == "standard":
        parsed_rate = _vat_rate(vat_rate_input)
        if parsed_rate is None:
            errors["vat_rate_pct"] = (
                "Mehrwertsteuersatz in Prozent, über 0 und höchstens 100."
            )
        else:
            vat_rate_pct = parsed_rate
    elif vat_rate_input:
        errors["vat_rate_pct"] = (
            "Mehrwertsteuersatz nur zusammen mit dem Mehrwertsteuer-Modus."
        )

    payment_days = _payment_days(_field(form, "payment_days"))
    if payment_days is None:
        errors["payment_days"] = (
            f"Zahlungsfrist in ganzen Tagen, zwischen {MIN_PAYMENT_DAYS} und "
            f"{MAX_PAYMENT_DAYS}."
        )

    invoice_prefix = _field(form, "invoice_prefix")
    if not INVOICE_PREFIX_PATTERN.match(invoice_prefix):
        errors["invoice_prefix"] = (
            "Rechnungspräfix: 2 bis 16 Zeichen, nur Grossbuchstaben, Ziffern "
            "und Bindestrich, beginnend mit Buchstabe oder Ziffer."
        )

    delivery_method = _enum(_field(form, "delivery_method"), DELIVERY_METHODS)
    if delivery_method is None:
        errors["delivery_method"] = "Unbekannte Zustellmethode."

    if errors:
        return {"policy": None, "errors": errors}
    parsed_policy_fields = {
        "effective_from": effective_from,
        "internal_price_chf_per_kwh": internal_price_rp / 100,
        "grid_fee_chf_per_kwh": grid_fee_rp / 100,
        "network_level": network_level,
        "distribution_model": distribution_model,
        "vat_mode": vat_mode,
        "vat_rate_pct": vat_rate_pct,
        "payment_days": payment_days,
        "invoice_prefix": invoice_prefix,
        "delivery_method": delivery_method,
    }
    return {
        "policy": {
            field: parsed_policy_fields[field] for field in EDITABLE_POLICY_FIELDS
        },
        "errors": {},
    }
