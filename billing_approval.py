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
_KWH_QUANTUM = Decimal("0.000001")

_FINGERPRINT_PATTERN = re.compile(r"[0-9a-f]{64}")

_ALLOWED_ITEM_TYPES = ("consumer_charge", "producer_credit", "rounding_adjustment")


class BillingApprovalError(RuntimeError):
    """A billing draft is incomplete, unreconciled, malformed, or not approvable."""


def today():
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
        return today()
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


def _require_finite_decimal(value, message):
    """Require one value to parse as a finite decimal; return it."""
    number = _as_decimal(value)
    if not number.is_finite():
        raise BillingApprovalError(message)
    return number


def _validated_difference(node, vnb_key, engine_key, difference_key):
    """Require finite vnb/engine evidence and a documented true zero gap.

    The difference must equal engine minus vnb at the 6-decimal persisted
    precision and must be exactly zero. Returns the engine value for the
    cross-check against billed quantities.
    """
    for key in (vnb_key, engine_key, difference_key):
        if key not in node:
            raise BillingApprovalError("Der dokumentierte Abgleich ist unvollständig.")
    vnb = _require_finite_decimal(
        node[vnb_key], "Der Abgleich enthält keinen gültigen Energiewert."
    )
    engine = _require_finite_decimal(
        node[engine_key], "Der Abgleich enthält keinen gültigen Energiewert."
    )
    difference = _as_decimal(node[difference_key])
    expected = (engine - vnb).quantize(_KWH_QUANTUM, rounding=ROUND_HALF_UP)
    if not difference.is_finite() or difference != expected or difference != 0:
        raise BillingApprovalError("Der Abgleich zwischen OpenLEG und VNB weicht ab.")
    return engine


def _require_canonical_reconciliation(reconciliation, consumption_kwh, production_kwh):
    """Require the complete reconcile_with_vnb evidence, cross-checked.

    Canonical contract: top-level vnb/engine totals for allocation and
    production with their differences, plus a ``per_participant`` and a
    ``production_per_participant`` dict whose entries carry vnb/engine/diff
    triples. The keys in each section must exactly match the billed
    consumer-charge or producer-credit participants (including zero lines),
    and every engine figure must match the billed quantities. Any missing,
    malformed, or inconsistent component fails closed.
    """
    if not isinstance(reconciliation, dict):
        raise BillingApprovalError(
            "Der Abrechnungsentwurf hat keinen dokumentierten Abgleich."
        )
    try:
        engine_allocated = _validated_difference(
            reconciliation,
            "vnb_allocated_kwh",
            "engine_allocated_kwh",
            "difference_kwh",
        )
        engine_production = _validated_difference(
            reconciliation,
            "vnb_production_kwh",
            "engine_production_kwh",
            "production_difference_kwh",
        )
    except ArithmeticError:
        raise BillingApprovalError(
            "Der Abgleich enthält einen ungültigen Energiewert."
        ) from None
    for section, billed_quantities in (
        ("per_participant", consumption_kwh),
        ("production_per_participant", production_kwh),
    ):
        entries = reconciliation.get(section)
        if not isinstance(entries, dict):
            raise BillingApprovalError(
                "Der Abgleich hat eine ungültige Teilnehmerstruktur."
            )
        if set(entries) != set(billed_quantities):
            raise BillingApprovalError(
                "Abgleich und Abrechnungspositionen decken nicht dieselben "
                "Teilnehmer ab."
            )
        for participant, entry in entries.items():
            if not isinstance(entry, dict):
                raise BillingApprovalError(
                    "Der Abgleich hat eine ungültige Teilnehmerstruktur."
                )
            try:
                engine_kwh = _validated_difference(
                    entry, "vnb_kwh", "engine_kwh", "difference_kwh"
                )
            except ArithmeticError:
                raise BillingApprovalError(
                    "Der Abgleich enthält einen ungültigen Energiewert."
                ) from None
            if engine_kwh != billed_quantities.get(participant, Decimal(0)):
                raise BillingApprovalError(
                    "Abgleich und Abrechnungspositionen melden unterschiedliche Mengen."
                )
    try:
        if engine_allocated != sum(consumption_kwh.values(), Decimal(0)):
            raise BillingApprovalError(
                "Abgleich und Abrechnungspositionen melden unterschiedliche Mengen."
            )
        if engine_production != sum(production_kwh.values(), Decimal(0)):
            raise BillingApprovalError(
                "Abgleich und Abrechnungspositionen melden unterschiedliche Mengen."
            )
    except ArithmeticError:
        raise BillingApprovalError(
            "Abgleich und Abrechnungspositionen melden unterschiedliche Mengen."
        ) from None


def _require_wellformed_line_items(line_items, internal_price):
    """Fail closed on anything but the exact shapes the billing engine emits.

    Consumer charges and producer credits carry a finite non-negative
    quantity, the policy internal unit price, and an amount equal to quantity
    times price at 6-decimal precision (non-negative for consumers,
    non-positive for producers). Non-rounding lines must be unique per
    (participant_id, item_type). The total billed consumer and producer
    quantities must conserve allocated energy
    within the aggregate rounding tolerance of 0.5e-6 kWh per non-rounding
    line. A rounding adjustment is permitted only when producer credits exist,
    it is assigned to the deterministic minimum producer participant id, its
    amount is exactly the negative of the non-rounding total at persisted
    6-decimal precision, its magnitude does not exceed the derived monetary
    residue bound, and at most one exists. Returns ``(line_items,
    consumption_kwh, production_kwh)`` with the billed quantity per participant
    for the reconciliation cross-check.
    """
    if isinstance(line_items, (list, tuple)) and not line_items:
        raise BillingApprovalError("Der Abrechnungsentwurf hat keine Positionen.")
    if not isinstance(line_items, (list, tuple)):
        raise BillingApprovalError(
            "Die Abrechnungspositionen haben eine ungültige Struktur."
        )
    consumption_kwh = {}
    production_kwh = {}
    rounding_items = []
    non_rounding_total = Decimal(0)
    consumer_count = 0
    producer_count = 0
    consumer_qty_total = Decimal(0)
    producer_qty_total = Decimal(0)
    seen_non_rounding_keys = set()

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
        item_type = item.get("item_type")
        if item_type not in _ALLOWED_ITEM_TYPES:
            raise BillingApprovalError(
                "Eine Abrechnungsposition hat einen ungültigen Typ."
            )
        amount = _require_finite_decimal(
            item.get("amount_chf"),
            "Eine Abrechnungsposition hat keinen gültigen Betrag.",
        )
        if item_type == "rounding_adjustment":
            if (
                item.get("quantity_kwh") is not None
                or item.get("unit_price_chf_per_kwh") is not None
            ):
                raise BillingApprovalError(
                    "Ein Rundungsausgleich darf weder Menge noch Preis tragen."
                )
            if _exceeds_precision(amount, 6):
                raise BillingApprovalError(
                    "Der Rundungsausgleich darf höchstens 6 Dezimalstellen haben."
                )
            rounding_items.append(item)
        else:
            quantity = _require_finite_decimal(
                item.get("quantity_kwh"),
                "Eine Abrechnungsposition hat keine gültige Menge.",
            )
            unit_price = _require_finite_decimal(
                item.get("unit_price_chf_per_kwh"),
                "Eine Abrechnungsposition hat keinen gültigen Preis.",
            )
            if quantity < 0 or unit_price < 0:
                raise BillingApprovalError(
                    "Menge und Preis einer Abrechnungsposition müssen nicht-negativ "
                    "sein."
                )
            if unit_price != internal_price:
                raise BillingApprovalError(
                    "Der Preis einer Abrechnungsposition weicht von der Richtlinie ab."
                )
            try:
                expected = (quantity * unit_price).quantize(
                    _KWH_QUANTUM, rounding=ROUND_HALF_UP
                )
            except ArithmeticError:
                raise BillingApprovalError(
                    "Eine Abrechnungsposition enthält einen ungültigen Betrag."
                ) from None
            if item_type == "producer_credit":
                expected = -expected
            if amount != expected:
                raise BillingApprovalError(
                    "Der Betrag einer Abrechnungsposition entspricht nicht Menge "
                    "mal Preis."
                )
            item_key = (participant_id, item_type)
            if item_key in seen_non_rounding_keys:
                raise BillingApprovalError(
                    "Jeder Teilnehmer darf pro Positionstyp nur eine Position haben."
                )
            seen_non_rounding_keys.add(item_key)
            if item_type == "consumer_charge":
                if amount < 0:
                    raise BillingApprovalError(
                        "Eine Verbrauchsladung muss einen nicht-negativen Betrag haben."
                    )
                consumption_kwh[participant_id] = (
                    consumption_kwh.get(participant_id, Decimal(0)) + quantity
                )
                consumer_count += 1
                try:
                    consumer_qty_total += quantity
                except ArithmeticError:
                    raise BillingApprovalError(
                        "Die abgerechneten Energiemengen sind nicht ausgeglichen."
                    ) from None
            else:
                if amount > 0:
                    raise BillingApprovalError(
                        "Eine Produzentengutschrift muss einen nicht-positiven "
                        "Betrag haben."
                    )
                production_kwh[participant_id] = (
                    production_kwh.get(participant_id, Decimal(0)) + quantity
                )
                producer_count += 1
                try:
                    producer_qty_total += quantity
                except ArithmeticError:
                    raise BillingApprovalError(
                        "Die abgerechneten Energiemengen sind nicht ausgeglichen."
                    ) from None
        if item_type != "rounding_adjustment":
            try:
                non_rounding_total += amount
            except ArithmeticError:
                raise BillingApprovalError(
                    "Die Abrechnungssumme kann nicht berechnet werden."
                ) from None

    try:
        non_rounding_count = consumer_count + producer_count
        quantity_tolerance = (_KWH_QUANTUM / Decimal(2)) * Decimal(non_rounding_count)
        quantity_mismatch = (consumer_qty_total - producer_qty_total).copy_abs()
    except ArithmeticError:
        raise BillingApprovalError(
            "Die abgerechneten Energiemengen sind nicht ausgeglichen."
        ) from None

    if quantity_mismatch > quantity_tolerance:
        raise BillingApprovalError(
            "Die abgerechneten Energiemengen sind nicht ausgeglichen."
        )

    try:
        max_rounding_abs = quantity_mismatch * internal_price + (
            _KWH_QUANTUM / Decimal(2)
        ) * Decimal(non_rounding_count)
    except ArithmeticError:
        raise BillingApprovalError(
            "Der Rundungsausgleich enthält einen ungültigen Betrag."
        ) from None

    producer_ids = list(production_kwh.keys())
    if not producer_ids:
        if rounding_items:
            raise BillingApprovalError(
                "Ein Rundungsausgleich ist ohne Produzentengutschrift nicht zulässig."
            )
        return line_items, consumption_kwh, production_kwh

    expected_rounding_participant = min(producer_ids, key=str)

    try:
        rounding_total = (
            sum(
                (_as_decimal(item.get("amount_chf")) for item in rounding_items),
                Decimal(0),
            )
            if rounding_items
            else Decimal(0)
        )
    except ArithmeticError:
        raise BillingApprovalError(
            "Der Rundungsausgleich enthält einen ungültigen Betrag."
        ) from None

    if non_rounding_total == 0:
        if rounding_items:
            raise BillingApprovalError(
                "Ein Rundungsausgleich ist bei ausgeglichenen Beträgen nicht "
                "erforderlich."
            )
    else:
        if len(rounding_items) != 1:
            raise BillingApprovalError(
                "Der Abrechnungsentwurf braucht genau einen Rundungsausgleich."
            )
        rounding = rounding_items[0]
        if rounding["participant_id"] != expected_rounding_participant:
            raise BillingApprovalError(
                "Der Rundungsausgleich muss dem kleinsten Produzenten zugeordnet sein."
            )
        try:
            expected_rounding_amount = (-non_rounding_total).quantize(
                _KWH_QUANTUM, rounding=ROUND_HALF_UP
            )
        except ArithmeticError:
            raise BillingApprovalError(
                "Der Rundungsausgleich enthält einen ungültigen Betrag."
            ) from None
        try:
            if rounding_total.copy_abs() > max_rounding_abs:
                raise BillingApprovalError(
                    "Der Rundungsausgleich übersteigt den zulässigen Restbetrag."
                )
        except ArithmeticError:
            raise BillingApprovalError(
                "Der Rundungsausgleich enthält einen ungültigen Betrag."
            ) from None
        if rounding_total != expected_rounding_amount:
            raise BillingApprovalError(
                "Der Rundungsausgleich schliesst den Betrag nicht korrekt ab."
            )

    return line_items, consumption_kwh, production_kwh


def _exceeds_precision(value, places):
    """Return whether a finite decimal carries more than ``places`` decimals."""
    return -value.as_tuple().exponent > places


def _require_valid_policy(policy, period_start, community_id):
    """Require every invoice-relevant policy field inside its domain.

    The value domains come from ``billing_policy`` so approval applies exactly
    the rules the policy form enforces, including the persisted precision of
    the money fields, and the snapshot must name the period's community. The
    snapshot is only read, never mutated. Returns the validated
    ``(vat_rate, payment_days, internal_price)`` triple.
    """
    try:
        validated = billing_policy.validate_persisted_policy(
            policy, period_start=period_start, community_id=community_id
        )
    except billing_policy.InvalidPersistedPolicy as exc:
        raise BillingApprovalError(str(exc)) from exc
    return (
        validated["vat_rate_pct"],
        validated["payment_days"],
        validated["internal_price_chf_per_kwh"],
    )


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
    community_id = period.get("community_id")
    if not isinstance(community_id, str) or not community_id.strip():
        raise BillingApprovalError(
            "Der Abrechnungsentwurf hat keine gültige Community-ID."
        )
    period_start, _period_end = _require_increasing_window(period)
    _require_fingerprint(period.get("input_fingerprint"))
    source_document_ids = _require_source_document_ids(
        period.get("source_document_ids")
    )
    vat_rate, payment_days, internal_price = _require_valid_policy(
        period.get("billing_policy_snapshot"), period_start, community_id
    )
    line_items, consumption_kwh, production_kwh = _require_wellformed_line_items(
        period.get("line_items"), internal_price
    )
    reconciliation = period.get("reconciliation")
    _require_canonical_reconciliation(reconciliation, consumption_kwh, production_kwh)

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
    for participant_id in sorted({item["participant_id"] for item in line_items}):
        items = [
            item for item in line_items if item.get("participant_id") == participant_id
        ]
        items.sort(key=lambda item: item.get("id") or 0)
        participant_rounding = next(
            (item for item in items if item.get("item_type") == "rounding_adjustment"),
            None,
        )
        participant_provenance = dict(provenance)
        participant_provenance["rounding_adjustment"] = (
            {
                "participant_id": participant_id,
                "amount_chf": _json_safe(participant_rounding["amount_chf"]),
            }
            if participant_rounding
            else None
        )
        net = sum((_as_decimal(item.get("amount_chf")) for item in items), Decimal(0))
        if not net.is_finite():
            raise BillingApprovalError(
                "Eine Abrechnungsposition hat keinen gültigen Betrag."
            )
        try:
            net = net.quantize(_CENT, rounding=ROUND_HALF_UP)
            vat = (net * vat_rate / Decimal(100)).quantize(
                _CENT, rounding=ROUND_HALF_UP
            )
        except ArithmeticError:
            raise BillingApprovalError(
                "Eine Abrechnungsposition hat keinen gültigen Betrag."
            ) from None
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
                "provenance_snapshot": participant_provenance,
                "input_fingerprint": period["input_fingerprint"],
                "source_document_ids": _json_safe(list(source_document_ids)),
            }
        )
    return snapshots
