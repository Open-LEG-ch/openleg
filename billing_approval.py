# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fail-closed invoice snapshots for approving a reconciled billing draft.

Approval never reconstructs historic choices from mutable tables: the draft
period carries its complete ``billing_policy_snapshot`` plus provenance, and
this module turns exactly that persisted state into one immutable invoice
snapshot per participant. Anything incomplete, unreconciled, malformed, or
outside the policy value domains of ``billing_policy`` is refused with
:class:`BillingApprovalError`.
"""

import re
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

import billing_policy

_CENT = Decimal("0.01")

_FINGERPRINT_PATTERN = re.compile(r"[0-9a-f]{64}")

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


def _parse_temporal(value, message):
    """Parse a date, datetime, or ISO string; refuse everything else."""
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
    raise BillingApprovalError(message)


def _require_order(start, end, message):
    """Require start before or at end; refuse incomparable time bases."""
    try:
        ordered = start <= end
    except TypeError:
        raise BillingApprovalError(
            "Die Zeitangaben haben inkonsistente Zeitformate."
        ) from None
    if not ordered:
        raise BillingApprovalError(message)


def _require_increasing_window(period):
    """Require a valid, strictly increasing billing window."""
    start = _parse_temporal(
        period.get("period_start"), "Der Periodenbeginn ist ungültig."
    )
    end = _parse_temporal(period.get("period_end"), "Das Periodenende ist ungültig.")
    _require_order(start, end, "Der Periodenbeginn muss vor dem Periodenende liegen.")
    if start == end:
        raise BillingApprovalError(
            "Der Periodenbeginn muss vor dem Periodenende liegen."
        )
    return start, end


def _require_fingerprint(value):
    """Require the SHA-256 hex shape billing_runner persists."""
    if not isinstance(value, str) or not _FINGERPRINT_PATTERN.fullmatch(value):
        raise BillingApprovalError(
            "Der Abrechnungsentwurf hat keinen gültigen Fingerprint."
        )
    return value


def _require_source_document_ids(value):
    """Require a non-empty list of non-empty source document id strings."""
    if not isinstance(value, (list, tuple)) or not value:
        raise BillingApprovalError("Der Abrechnungsentwurf hat keine Quelldokumente.")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise BillingApprovalError("Ein Quelldokument hat eine ungültige ID.")
    return list(value)


def _require_finite_zero_difference(value):
    """Require one recorded difference to be finite and exactly zero."""
    difference = _as_decimal(value)
    if not difference.is_finite() or difference != 0:
        raise BillingApprovalError("Der Abgleich zwischen OpenLEG und VNB weicht ab.")


def _require_canonical_reconciliation(reconciliation):
    """Require the exact complete shape billing_runner persists, all gaps zero.

    Canonical contract: top-level ``difference_kwh`` and
    ``production_difference_kwh`` plus a ``per_participant`` and a
    ``production_per_participant`` dict whose entries are dicts carrying their
    own ``difference_kwh``. Every difference must be finite and exactly zero;
    any missing or malformed component fails closed.
    """
    if not isinstance(reconciliation, dict):
        raise BillingApprovalError(
            "Der Abrechnungsentwurf hat keinen dokumentierten Abgleich."
        )
    for key in ("difference_kwh", "production_difference_kwh"):
        if key not in reconciliation:
            raise BillingApprovalError("Der dokumentierte Abgleich ist unvollständig.")
        _require_finite_zero_difference(reconciliation[key])
    for section in _RECONCILIATION_SECTIONS:
        entries = reconciliation.get(section)
        if not isinstance(entries, dict):
            raise BillingApprovalError(
                "Der Abgleich hat eine ungültige Teilnehmerstruktur."
            )
        for entry in entries.values():
            if not isinstance(entry, dict) or "difference_kwh" not in entry:
                raise BillingApprovalError(
                    "Der Abgleich hat eine ungültige Teilnehmerstruktur."
                )
            _require_finite_zero_difference(entry["difference_kwh"])


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


def _require_valid_policy(policy, period_start):
    """Require every invoice-relevant policy field inside its domain.

    The value domains come from ``billing_policy`` so approval applies exactly
    the rules the policy form enforces. The snapshot is only read, never
    mutated. Returns the validated ``(vat_rate, payment_days)`` pair.
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
    tariff_id = policy.get("tariff_id")
    if isinstance(tariff_id, bool) or not isinstance(tariff_id, int) or tariff_id <= 0:
        raise BillingApprovalError("Die Tarif-ID der Richtlinie ist ungültig.")
    max_price_chf = billing_policy.MAX_PRICE_RP / Decimal(100)
    for field in ("internal_price_chf_per_kwh", "grid_fee_chf_per_kwh"):
        price = _as_decimal(policy.get(field))
        if not price.is_finite() or price < 0 or price > max_price_chf:
            raise BillingApprovalError(
                "Ein Energiepreis der Richtlinie liegt ausserhalb des zulässigen "
                "Bereichs."
            )
    effective_from = _parse_temporal(
        policy.get("effective_from"),
        "Das Inkrafttretungsdatum der Richtlinie ist ungültig.",
    )
    _require_order(
        effective_from,
        period_start,
        "Die Richtlinie gilt noch nicht zum Periodenbeginn.",
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
    period_start, _period_end = _require_increasing_window(period)
    _require_fingerprint(period.get("input_fingerprint"))
    source_document_ids = _require_source_document_ids(
        period.get("source_document_ids")
    )
    reconciliation = period.get("reconciliation")
    _require_canonical_reconciliation(reconciliation)
    vat_rate, payment_days = _require_valid_policy(
        period.get("billing_policy_snapshot"), period_start
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
