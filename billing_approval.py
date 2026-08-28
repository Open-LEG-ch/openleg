# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fail-closed invoice snapshots for approving a reconciled billing draft.

Approval never reconstructs historic choices from mutable tables: the draft
period carries its complete ``billing_policy_snapshot`` plus provenance, and
this module turns exactly that persisted state into one immutable invoice
snapshot per participant. Anything incomplete, unreconciled, malformed, or
outside the policy value domains of ``billing_policy`` is refused with
:class:`BillingApprovalError`.
"""

from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

import billing_policy

_CENT = Decimal("0.01")

REQUIRED_POLICY_FIELDS = (
    "tariff_id",
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

_RECONCILIATION_SECTIONS = ("per_participant", "production_per_participant")


class BillingApprovalError(RuntimeError):
    """A billing draft is incomplete, unreconciled, malformed, or not approvable."""


def _today():
    """Return the current Swiss local date for default issue dates."""
    return datetime.now(ZoneInfo("Europe/Zurich")).date()


def _json_safe(value):
    """Convert decimals and temporals so snapshots survive JSONB unchanged."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _as_decimal(value):
    """Parse a numeric value; anything unparseable becomes NaN (refused)."""
    try:
        return Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        return Decimal("NaN")


def _require_issue_date(issue_date):
    """Accept a plain date; refuse datetimes, strings, numbers, and bools."""
    if issue_date is None:
        return _today()
    if (
        isinstance(issue_date, bool)
        or not isinstance(issue_date, date)
        or isinstance(issue_date, datetime)
    ):
        raise BillingApprovalError("Das Rechnungsdatum ist ungültig.")
    return issue_date


def _collect_differences(node, differences, key=""):
    """Collect every nested value stored under a ``*difference_kwh`` key."""
    if isinstance(node, dict):
        for child_key, child in node.items():
            _collect_differences(child, differences, str(child_key))
    elif key.endswith("difference_kwh"):
        differences.append(node)


def _require_balanced_reconciliation(reconciliation):
    """Require at least one recorded difference and all of them exactly zero."""
    differences = []
    if isinstance(reconciliation, dict):
        _collect_differences(reconciliation, differences)
    if not differences:
        raise BillingApprovalError(
            "Der Abrechnungsentwurf hat keinen dokumentierten Abgleich."
        )
    if any(
        not difference.is_finite() or difference != 0
        for difference in map(_as_decimal, differences)
    ):
        raise BillingApprovalError("Der Abgleich zwischen OpenLEG und VNB weicht ab.")


def _reconciled_participants(reconciliation):
    """Union of participants named by the reconciliation; malformed is refused."""
    participants = set()
    for section in _RECONCILIATION_SECTIONS:
        entries = reconciliation.get(section)
        if entries is None:
            continue
        if not isinstance(entries, dict):
            raise BillingApprovalError(
                "Der Abgleich hat eine ungültige Teilnehmerstruktur."
            )
        participants.update(entries)
    return participants


def _require_wellformed_line_items(line_items):
    """Require a non-empty list of dicts with non-empty string participant ids."""
    if isinstance(line_items, (list, tuple)) and not line_items:
        raise BillingApprovalError("Der Abrechnungsentwurf hat keine Positionen.")
    if not isinstance(line_items, (list, tuple)):
        raise BillingApprovalError(
            "Die Abrechnungspositionen haben eine ungültige Struktur."
        )
    for item in line_items:
        if not isinstance(item, dict):
            raise BillingApprovalError(
                "Eine Abrechnungsposition hat eine ungültige Struktur."
            )
        participant_id = item.get("participant_id")
        if not isinstance(participant_id, str) or not participant_id.strip():
            raise BillingApprovalError(
                "Eine Abrechnungsposition hat keine gültige Teilnehmer-ID."
            )
    return line_items


def _require_valid_policy(policy):
    """Require every invoice-relevant policy field inside its domain.

    The value domains come from ``billing_policy`` so approval applies exactly
    the rules the policy form enforces. Returns the validated
    ``(vat_rate, payment_days)`` pair.
    """
    if not isinstance(policy, dict) or not policy:
        raise BillingApprovalError(
            "Der Abrechnungsentwurf hat keine Richtlinien-Kopie."
        )
    missing = [field for field in REQUIRED_POLICY_FIELDS if policy.get(field) is None]
    if missing:
        raise BillingApprovalError(
            "Die Richtlinien-Kopie ist unvollständig: " + ", ".join(missing)
        )
    if policy.get("network_level") not in billing_policy.NETWORK_LEVELS:
        raise BillingApprovalError("Die Netzebene der Richtlinie ist ungültig.")
    if policy.get("distribution_model") not in billing_policy.DISTRIBUTION_MODELS:
        raise BillingApprovalError("Das Verteilmodell der Richtlinie ist ungültig.")
    if policy.get("delivery_method") not in billing_policy.DELIVERY_METHODS:
        raise BillingApprovalError("Die Zustellmethode der Richtlinie ist ungültig.")
    invoice_prefix = policy.get("invoice_prefix")
    if not isinstance(
        invoice_prefix, str
    ) or not billing_policy.INVOICE_PREFIX_PATTERN.match(invoice_prefix):
        raise BillingApprovalError("Das Rechnungspräfix der Richtlinie ist ungültig.")
    payment_days = policy.get("payment_days")
    if (
        isinstance(payment_days, bool)
        or not isinstance(payment_days, int)
        or not billing_policy.MIN_PAYMENT_DAYS
        <= payment_days
        <= billing_policy.MAX_PAYMENT_DAYS
    ):
        raise BillingApprovalError("Die Zahlungsfrist der Richtlinie ist ungültig.")
    vat_mode = policy.get("vat_mode")
    if vat_mode not in billing_policy.VAT_MODES:
        raise BillingApprovalError(
            "Der Mehrwertsteuer-Modus der Richtlinie ist ungültig."
        )
    vat_rate = _as_decimal(policy.get("vat_rate_pct"))
    if vat_mode == "none":
        if not vat_rate.is_finite() or vat_rate != 0:
            raise BillingApprovalError("Ohne Mehrwertsteuer muss der Satz 0 sein.")
        vat_rate = Decimal(0)
    elif (
        not vat_rate.is_finite()
        or vat_rate <= 0
        or vat_rate > billing_policy.MAX_VAT_RATE_PCT
    ):
        raise BillingApprovalError(
            "Der Mehrwertsteuersatz der Richtlinie ist ungültig."
        )
    return vat_rate, payment_days


def prepare_invoice_snapshots(period, issue_date=None):
    """Build one immutable invoice snapshot per billed participant.

    Fails closed with :class:`BillingApprovalError` unless the period is a
    draft with provenance, a complete and valid policy snapshot, well-formed
    line items, and an exactly balanced reconciliation covering precisely the
    billed participants.
    """
    issue_date = _require_issue_date(issue_date)
    if not isinstance(period, dict) or period.get("status") != "draft":
        raise BillingApprovalError("Nur ein Entwurf kann freigegeben werden.")
    if not period.get("input_fingerprint"):
        raise BillingApprovalError("Der Abrechnungsentwurf hat keinen Fingerprint.")
    source_document_ids = period.get("source_document_ids")
    if not source_document_ids:
        raise BillingApprovalError("Der Abrechnungsentwurf hat keine Quelldokumente.")
    reconciliation = period.get("reconciliation")
    _require_balanced_reconciliation(reconciliation)
    vat_rate, payment_days = _require_valid_policy(
        period.get("billing_policy_snapshot")
    )
    line_items = _require_wellformed_line_items(period.get("line_items"))

    billed = {item["participant_id"] for item in line_items}
    if billed != _reconciled_participants(reconciliation):
        raise BillingApprovalError(
            "Abgleich und Abrechnungspositionen decken nicht dieselben Teilnehmer ab."
        )

    policy = period["billing_policy_snapshot"]
    due_date = issue_date + timedelta(days=payment_days)
    provenance = {
        "input_fingerprint": period["input_fingerprint"],
        "source_document_ids": _json_safe(list(source_document_ids)),
        "reconciliation": _json_safe(reconciliation),
        "period_start": _json_safe(period.get("period_start")),
        "period_end": _json_safe(period.get("period_end")),
    }

    snapshots = []
    for participant_id in sorted(billed):
        items = [
            item for item in line_items if item.get("participant_id") == participant_id
        ]
        items.sort(key=lambda item: item.get("id") or 0)
        net = sum((_as_decimal(item.get("amount_chf")) for item in items), Decimal(0))
        if not net.is_finite():
            raise BillingApprovalError(
                "Eine Abrechnungsposition hat keinen gültigen Betrag."
            )
        net = net.quantize(_CENT, rounding=ROUND_HALF_UP)
        vat = (net * vat_rate / Decimal(100)).quantize(_CENT, rounding=ROUND_HALF_UP)
        snapshots.append(
            {
                "participant_id": participant_id,
                "line_items_snapshot": _json_safe(items),
                "net_chf": net,
                "vat_rate_pct": vat_rate,
                "vat_chf": vat,
                "gross_chf": net + vat,
                "issue_date": issue_date,
                "due_date": due_date,
                "policy_snapshot": _json_safe(policy),
                "provenance_snapshot": provenance,
                "input_fingerprint": period["input_fingerprint"],
                "source_document_ids": _json_safe(list(source_document_ids)),
            }
        )
    return snapshots
