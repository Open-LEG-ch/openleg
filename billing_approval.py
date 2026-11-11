# SPDX-License-Identifier: AGPL-3.0-or-later
"""Transactional approval and fail-closed snapshots for a reconciled draft.

Approval never reconstructs historic choices from mutable tables: the draft
period carries its complete ``billing_policy_snapshot`` plus provenance, and
this module turns exactly that persisted state into one immutable invoice
snapshot per participant. Anything incomplete, unreconciled, malformed, or
outside the policy value domains of ``billing_policy`` is refused with
:class:`BillingApprovalError`.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import cast
from zoneinfo import ZoneInfo

import billing_policy
import quartierakku
from billing_types import (
    BatteryConfig,
    BatterySnapshot,
    BillingConnection,
    BillingCursor,
    InvoiceSnapshot,
    LineItem,
    Period,
)
from store import billing as billing_store

logger = logging.getLogger(__name__)

_CENT = Decimal("0.01")
_KWH_QUANTUM = Decimal("0.000001")

_FINGERPRINT_PATTERN = re.compile(r"[0-9a-f]{64}")

_ALLOWED_ITEM_TYPES = (
    "consumer_charge",
    "producer_credit",
    "rounding_adjustment",
    "settlement_fee",
    "battery_cost_share",
)

_ENERGY_ITEM_TYPES = ("consumer_charge", "producer_credit")


class BillingApprovalError(RuntimeError):
    """A billing draft is incomplete, unreconciled, malformed, or not approvable."""


def today() -> date:
    """Return the current Swiss local date for default issue dates."""
    return datetime.now(ZoneInfo("Europe/Zurich")).date()


def _json_safe(value: object) -> object:
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


def _as_decimal(value: object) -> Decimal:
    """Parse a numeric value; anything unparseable becomes NaN (refused)."""
    try:
        return Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        return Decimal("NaN")


def _require_issue_date(issue_date: object) -> date:
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


def _parse_temporal(value: object, message: str) -> date:
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


def _require_order(start: date, end: date, message: str) -> None:
    """Require start before or at end; refuse incomparable time bases."""
    try:
        ordered = start <= end
    except TypeError:
        raise BillingApprovalError(
            "Die Zeitangaben haben inkonsistente Zeitformate."
        ) from None
    if not ordered:
        raise BillingApprovalError(message)


def _require_increasing_window(period: Period) -> tuple[date, date]:
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


def _require_fingerprint(value: object) -> str:
    """Require the SHA-256 hex shape billing_runner persists."""
    if not isinstance(value, str) or not _FINGERPRINT_PATTERN.fullmatch(value):
        raise BillingApprovalError(
            "Der Abrechnungsentwurf hat keinen gültigen Fingerprint."
        )
    return value


def _require_source_document_ids(value: object) -> list[str]:
    """Require a non-empty list of non-empty source document id strings."""
    if not isinstance(value, (list, tuple)) or not value:
        raise BillingApprovalError("Der Abrechnungsentwurf hat keine Quelldokumente.")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise BillingApprovalError("Ein Quelldokument hat eine ungültige ID.")
    return cast(list[str], list(value))


def _require_finite_decimal(value: object, message: str) -> Decimal:
    """Require one value to parse as a finite decimal; return it."""
    number = _as_decimal(value)
    if not number.is_finite():
        raise BillingApprovalError(message)
    return number


def _validated_difference(
    node: Mapping[str, object],
    vnb_key: str,
    engine_key: str,
    difference_key: str,
    *,
    vnb_basis: bool = False,
) -> Decimal:
    """Require the documented model gap at six-decimal persisted precision.

    Legacy billing requires a zero gap and prices the engine quantity. An
    independently verified E66 certificate permits a truthful model gap and
    prices the VNB quantity.
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
    if (
        not difference.is_finite()
        or difference != expected
        or (not vnb_basis and difference != 0)
    ):
        raise BillingApprovalError("Der Abgleich zwischen OpenLEG und VNB weicht ab.")
    return vnb if vnb_basis else engine


def _require_canonical_reconciliation(
    reconciliation: object,
    consumption_kwh: Mapping[str, Decimal],
    production_kwh: Mapping[str, Decimal],
    *,
    vnb_basis: bool = False,
) -> None:
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
            vnb_basis=vnb_basis,
        )
        engine_production = _validated_difference(
            reconciliation,
            "vnb_production_kwh",
            "engine_production_kwh",
            "production_difference_kwh",
            vnb_basis=vnb_basis,
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
                    entry,
                    "vnb_kwh",
                    "engine_kwh",
                    "difference_kwh",
                    vnb_basis=vnb_basis,
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


def _validated_line_item(
    item: object, internal_price: Decimal, settlement_fee: Decimal
) -> tuple[str, str, Decimal, Decimal | None]:
    """Require one line item's exact shape and money; return the values.

    Validates the dict structure, the participant id (a non-empty string
    without surrounding whitespace), the allowed item type, and a finite
    amount. A rounding adjustment carries neither quantity nor price and at
    most 6 amount decimals. A standard line carries a finite non-negative
    quantity and unit price equal to the policy internal price, with an
    amount equal to quantity times price at 6-decimal precision (negated
    for producer credits). A settlement fee line prices the settled energy
    at the policy settlement fee instead. Returns ``(participant_id,
    item_type, amount, quantity)`` for the aggregation; ``quantity`` is
    ``None`` for rounding adjustments.
    """
    if not isinstance(item, dict):
        raise BillingApprovalError(
            "Eine Abrechnungsposition hat eine ungültige Struktur."
        )
    participant_id = item.get("participant_id")
    if (
        not isinstance(participant_id, str)
        or not participant_id.strip()
        or participant_id != participant_id.strip()
    ):
        raise BillingApprovalError(
            "Eine Abrechnungsposition hat keine gültige Teilnehmer-ID."
        )
    item_type = item.get("item_type")
    if item_type not in _ALLOWED_ITEM_TYPES:
        raise BillingApprovalError("Eine Abrechnungsposition hat einen ungültigen Typ.")
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
        return participant_id, cast(str, item_type), amount, None
    if item_type == "battery_cost_share":
        if (
            item.get("quantity_kwh") is not None
            or item.get("unit_price_chf_per_kwh") is not None
        ):
            raise BillingApprovalError(
                "Ein Quartierakku-Anteil darf weder Menge noch Preis tragen."
            )
        if amount < 0:
            raise BillingApprovalError(
                "Ein Quartierakku-Anteil darf keinen negativen Betrag haben."
            )
        if _exceeds_precision(amount, 6):
            raise BillingApprovalError(
                "Der Quartierakku-Anteil darf höchstens 6 Dezimalstellen haben."
            )
        return participant_id, cast(str, item_type), amount, None
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
            "Menge und Preis einer Abrechnungsposition müssen nicht-negativ sein."
        )
    policy_price = settlement_fee if item_type == "settlement_fee" else internal_price
    if unit_price != policy_price:
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
            "Der Betrag einer Abrechnungsposition entspricht nicht Menge mal Preis."
        )
    return participant_id, cast(str, item_type), amount, quantity


def _require_rounding_adjustment(
    producer_ids: Sequence[str],
    rounding_items: Sequence[LineItem],
    non_rounding_total: Decimal,
    max_rounding_abs: Decimal,
) -> None:
    """Require the exact rounding adjustment the billed totals imply.

    With producer credits present, the balanced case forbids any rounding
    adjustment; otherwise exactly one must exist, assigned to the smallest
    producer participant id, with a summed amount that equals the negated
    non-rounding total at 6-decimal precision and does not exceed the
    derived monetary residue bound.
    """
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


def _require_wellformed_line_items(
    line_items: object, internal_price: Decimal, settlement_fee: Decimal
) -> tuple[Sequence[LineItem], dict[str, Decimal], dict[str, Decimal]]:
    """Fail closed on anything but the exact shapes the billing engine emits.

    Consumer charges and producer credits carry a finite non-negative
    quantity, the policy internal unit price, and an amount equal to quantity
    times price at 6-decimal precision (non-negative for consumers,
    non-positive for producers). A settlement fee line carries the policy
    settlement fee price and the same quantity as the participant's billed
    energy; its amounts stay outside the rounding pool. Non-rounding lines
    must be unique per (participant_id, item_type). The total billed consumer
    and producer quantities must conserve allocated energy within the
    aggregate rounding tolerance of 0.5e-6 kWh per non-rounding line. A
    rounding adjustment is permitted only when producer credits exist, it is
    assigned to the deterministic minimum producer participant id, its amount
    is exactly the negative of the non-rounding total at persisted 6-decimal
    precision, its magnitude does not exceed the derived monetary residue
    bound, and at most one exists. Returns ``(line_items, consumption_kwh,
    production_kwh)`` with the billed quantity per participant for the
    reconciliation cross-check.
    """
    if isinstance(line_items, (list, tuple)) and not line_items:
        raise BillingApprovalError("Der Abrechnungsentwurf hat keine Positionen.")
    if not isinstance(line_items, (list, tuple)):
        raise BillingApprovalError(
            "Die Abrechnungspositionen haben eine ungültige Struktur."
        )
    consumption_kwh: dict[str, Decimal] = {}
    production_kwh: dict[str, Decimal] = {}
    settlement_quantities = {}
    battery_items = []
    rounding_items = []
    non_rounding_total = Decimal(0)
    consumer_count = 0
    producer_count = 0
    consumer_qty_total = Decimal(0)
    producer_qty_total = Decimal(0)
    seen_non_rounding_keys = set()

    for item in line_items:
        participant_id, item_type, amount, quantity = _validated_line_item(
            item, internal_price, settlement_fee
        )
        if item_type == "rounding_adjustment":
            rounding_items.append(item)
        elif item_type == "battery_cost_share":
            battery_items.append(item)
        else:
            # The validator returns a quantity for every energy/fee line.
            quantity = cast(Decimal, quantity)
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
            elif item_type == "settlement_fee":
                if amount < 0:
                    raise BillingApprovalError(
                        "Eine Abrechnungsposition muss einen nicht-negativen "
                        "Betrag haben."
                    )
                settlement_quantities[participant_id] = quantity
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
        if item_type not in (
            "rounding_adjustment",
            "settlement_fee",
            "battery_cost_share",
        ):
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

    if settlement_fee == 0 and settlement_quantities:
        raise BillingApprovalError("Das Abrechnungsentgelt passt nicht zur Richtlinie.")
    if settlement_fee != 0:
        for participant_id, fee_quantity in settlement_quantities.items():
            if consumption_kwh.get(participant_id) != fee_quantity:
                raise BillingApprovalError(
                    "Das Abrechnungsentgelt passt nicht zur abgerechneten Energie."
                )
        for participant_id, billed_quantity in consumption_kwh.items():
            if settlement_quantities.get(participant_id) != billed_quantity:
                raise BillingApprovalError(
                    "Das Abrechnungsentgelt passt nicht zur abgerechneten Energie."
                )

    producer_ids = list(production_kwh.keys())
    if not producer_ids:
        if rounding_items:
            raise BillingApprovalError(
                "Ein Rundungsausgleich ist ohne Produzentengutschrift nicht zulässig."
            )
        return line_items, consumption_kwh, production_kwh

    _require_rounding_adjustment(
        producer_ids, rounding_items, non_rounding_total, max_rounding_abs
    )

    return line_items, consumption_kwh, production_kwh


def _require_battery_energy_snapshot(
    battery_snapshot: BatterySnapshot,
    battery: BatteryConfig,
    consumption_kwh: Mapping[str, Decimal],
    production_kwh: Mapping[str, Decimal],
    internal_price: Decimal,
) -> None:
    """Reconcile the frozen source attribution with approved energy lines."""
    participant_id = battery["participant_id"]
    energy = battery_snapshot.get("energy")
    attribution = battery_snapshot.get("attribution")
    if not isinstance(energy, dict) or not isinstance(attribution, dict):
        raise BillingApprovalError(
            "Die Energieaufteilung des Quartierakkus ist unvollständig."
        )

    def quantity(
        container: Mapping[str, object], key: str, *, signed: bool = False
    ) -> Decimal:
        value = _as_decimal(container.get(key))
        if (
            not value.is_finite()
            or _exceeds_precision(value, 6)
            or (not signed and value < 0)
        ):
            raise BillingApprovalError(
                "Die Energieaufteilung des Quartierakkus ist ungültig."
            )
        return value

    charged = quantity(energy, "charged_kwh")
    discharged = quantity(energy, "discharged_kwh")
    difference = quantity(energy, "charge_discharge_difference_kwh", signed=True)
    allocated = quantity(energy, "allocated_battery_kwh")
    unallocated = quantity(energy, "unallocated_discharge_kwh")
    if difference != charged - discharged or unallocated != discharged - allocated:
        raise BillingApprovalError(
            "Die Energiebilanz des Quartierakkus ist nicht ausgeglichen."
        )

    expected_consumers = set(consumption_kwh) - {participant_id}
    if set(attribution) != expected_consumers:
        raise BillingApprovalError(
            "Die Energieaufteilung des Quartierakkus deckt die Teilnehmer nicht ab."
        )
    attributed_battery = Decimal(0)
    for consumer_id in sorted(expected_consumers):
        values = attribution.get(consumer_id)
        if not isinstance(values, dict):
            raise BillingApprovalError(
                "Die Energieaufteilung des Quartierakkus ist ungültig."
            )
        direct = quantity(values, "direct_solar_kwh")
        battery_kwh = quantity(values, "battery_kwh")
        value_chf = quantity(values, "value_chf")
        expected_value = (battery_kwh * internal_price).quantize(
            _CENT, rounding=ROUND_HALF_UP
        )
        if value_chf != expected_value:
            raise BillingApprovalError(
                "Der Wert der Quartierakku-Energie stimmt nicht mit dem Entwurf überein."
            )
        if direct + battery_kwh != consumption_kwh[consumer_id]:
            raise BillingApprovalError(
                "Die Energiequellen des Quartierakkus stimmen für einen "
                "Teilnehmer nicht mit dem Entwurf überein."
            )
        attributed_battery += battery_kwh

    if (
        attributed_battery != allocated
        or production_kwh.get(participant_id) != allocated
        or participant_id not in consumption_kwh
        or consumption_kwh[participant_id] > charged
    ):
        raise BillingApprovalError(
            "Die Energieaufteilung des Quartierakkus stimmt nicht mit dem Entwurf überein."
        )


def _require_battery_cost_shares(
    period: Period,
    community_id: str,
    line_items: Sequence[LineItem],
    consumption_kwh: Mapping[str, Decimal],
    production_kwh: Mapping[str, Decimal],
) -> None:
    """Require the battery cost shares the frozen asset config implies.

    A draft without a battery snapshot may carry no battery lines. With one,
    the config must be complete, name this community, and cover every billed
    participant with exactly one cost-share line whose amount equals the
    frozen annual cost times the frozen share at persisted precision.
    """
    battery_snapshot = period.get("battery_snapshot")
    if battery_snapshot is None:
        if any(item.get("item_type") == "battery_cost_share" for item in line_items):
            raise BillingApprovalError(
                "Ein Quartierakku-Anteil passt nicht zum Entwurf."
            )
        return
    try:
        battery = quartierakku.validate_battery_config(battery_snapshot)
    except quartierakku.InvalidBatteryConfig as exc:
        raise BillingApprovalError(str(exc)) from exc
    if battery.get("community_id") != community_id:
        raise BillingApprovalError(
            "Der Quartierakku gehört nicht zur Community des Entwurfs."
        )
    expected_fraction = quartierakku.period_fraction(
        period.get("period_start"), period.get("period_end")
    )
    period_start = period.get("period_start")
    period_end = period.get("period_end")
    period_start_text = (
        period_start.isoformat() if hasattr(period_start, "isoformat") else period_start
    )
    period_end_text = (
        period_end.isoformat() if hasattr(period_end, "isoformat") else period_end
    )
    if (
        battery_snapshot.get("period_start") != period_start_text
        or battery_snapshot.get("period_end") != period_end_text
        or _as_decimal(battery_snapshot.get("period_fraction")) != expected_fraction
    ):
        raise BillingApprovalError(
            "Die Abrechnungsperiode des Quartierakkus stimmt nicht mit dem Entwurf überein."
        )
    battery_participant_id = battery.get("participant_id")
    billed = {
        item["participant_id"]
        for item in line_items
        if item["participant_id"] != battery_participant_id
    }
    share_lines = [
        item for item in line_items if item.get("item_type") == "battery_cost_share"
    ]
    share_participants = [item["participant_id"] for item in share_lines]
    if (
        len(share_participants) != len(set(share_participants))
        or set(share_participants) != billed
    ):
        raise BillingApprovalError(
            "Der Quartierakku ist unvollständig konfiguriert: Jeder "
            "beteiligte Teilnehmer braucht genau einen Anteil."
        )
    for item in share_lines:
        share = battery["shares"].get(item["participant_id"])
        if share is None:
            raise BillingApprovalError(
                "Der Quartierakku ist unvollständig konfiguriert: Teilnehmer "
                f"{item['participant_id']} hat keinen Anteil."
            )
        expected = quartierakku.period_cost_share_chf(
            battery["annual_cost_chf"],
            share,
            period.get("period_start"),
            period.get("period_end"),
        )
        amount = _as_decimal(item.get("amount_chf"))
        if amount != expected:
            raise BillingApprovalError(
                "Der Quartierakku-Anteil entspricht nicht der gespeicherten "
                "Kostenaufteilung."
            )
    _require_battery_energy_snapshot(
        battery_snapshot,
        battery,
        consumption_kwh,
        production_kwh,
        _as_decimal(period["billing_policy_snapshot"]["internal_price_chf_per_kwh"]),
    )


def _exceeds_precision(value: Decimal, places: int) -> bool:
    """Return whether a finite decimal carries more than ``places`` decimals."""
    # All callers check finiteness before inspecting the integer exponent.
    return -cast(int, value.as_tuple().exponent) > places


def _require_valid_policy(
    policy: object, period_start: date, community_id: str
) -> tuple[Decimal, int, Decimal, Decimal]:
    """Require every invoice-relevant policy field inside its domain.

    The value domains come from ``billing_policy`` so approval applies exactly
    the rules the policy form enforces, including the persisted precision of
    the money fields, and the snapshot must name the period's community. The
    snapshot is only read, never mutated. Returns the validated
    ``(vat_rate, payment_days, internal_price, settlement_fee)`` tuple; a
    snapshot frozen before the settlement fee existed reads as zero.
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
        validated["settlement_fee_chf_per_kwh"],
    )


def prepare_invoice_snapshots(
    period: object, issue_date: object = None, *, calculated_values: object = None
) -> list[InvoiceSnapshot]:
    """Build one immutable invoice snapshot per billed participant.

    Fails closed with :class:`BillingApprovalError` unless the period is a
    draft with provenance, a complete and valid policy snapshot, well-formed
    line items, and an exactly balanced reconciliation covering precisely the
    billed participants.
    """
    issue_date = _require_issue_date(issue_date)
    if not isinstance(period, dict) or period.get("status") != "draft":
        raise BillingApprovalError("Nur ein Entwurf kann freigegeben werden.")
    # Describe the persisted row; the checks below still validate untrusted values.
    period = cast(Period, period)
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
    vat_rate, payment_days, internal_price, settlement_fee = _require_valid_policy(
        period.get("billing_policy_snapshot"), period_start, community_id
    )
    line_items, consumption_kwh, production_kwh = _require_wellformed_line_items(
        period.get("line_items"), internal_price, settlement_fee
    )
    reconciliation = period.get("reconciliation")
    if isinstance(reconciliation, dict) and any(
        reconciliation.get(key) is not None
        for key in ("vnb_imbalance_release", "vnb_imbalance_tolerance_ratio")
    ):
        raise BillingApprovalError(
            "Der Entwurf enthält eine veraltete Toleranzregel. Erstellen Sie ihn neu."
        )
    vnb_basis = (
        isinstance(reconciliation, dict) and "precision_policy" in reconciliation
    )
    e66_source = (
        isinstance(calculated_values, dict)
        and calculated_values.get("format_version") == "e66/1"
    )
    if vnb_basis or e66_source:
        import billing_reconciliation

        try:
            billing_reconciliation.validate_billed_source(
                period,
                calculated_values,
                consumption_kwh,
                production_kwh,
                require_certificate=vnb_basis,
            )
        except billing_reconciliation.ReconciliationError:
            raise BillingApprovalError(
                "Der VNB-Beleg bestätigt diese Mengen nicht."
            ) from None
    _require_canonical_reconciliation(
        reconciliation, consumption_kwh, production_kwh, vnb_basis=vnb_basis
    )
    _require_battery_cost_shares(
        period, community_id, line_items, consumption_kwh, production_kwh
    )

    policy = period["billing_policy_snapshot"]
    due_date = issue_date + timedelta(days=payment_days)
    provenance: dict[str, object] = {
        "input_fingerprint": period["input_fingerprint"],
        "source_document_ids": _json_safe(list(source_document_ids)),
        "reconciliation": _json_safe(
            {
                key: value
                for key, value in reconciliation.items()
                if key != "participant_registrations"
            }
        ),
        "period_start": _json_safe(period.get("period_start")),
        "period_end": _json_safe(period.get("period_end")),
    }
    snapshots: list[InvoiceSnapshot] = []
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
        participant_provenance["participant_registration_id"] = reconciliation.get(
            "participant_registrations", {}
        ).get(participant_id)
        if vnb_basis:
            participant_provenance["reconciliation"] = {
                **cast(dict[str, object], provenance["reconciliation"]),
                "precision_policy": {
                    key: value
                    for key, value in reconciliation["precision_policy"].items()
                    if key != "intervals"
                },
            }
        if reconciliation.get("source_evidence"):
            source = reconciliation["source_evidence"]
            metadata = source["metadata"]
            public_metadata: dict[str, object] = {
                key: cast(Mapping[str, object], metadata)[key]
                for key in (
                    "document_status",
                    "evidence_sha256",
                    "sender_id",
                    "receiver_id",
                    "document_created_at",
                    "vnb_community_id",
                    "precision",
                    "quality_policy",
                    "channel_balance_policy",
                    "products",
                )
                if key in metadata
            }
            public_metadata["mappings"] = [
                mapping
                for mapping in metadata["mappings"]
                if mapping["building_id"] == participant_id
            ]
            participant_provenance["reconciliation"] = {
                **cast(dict[str, object], participant_provenance["reconciliation"]),
                "per_participant": {
                    key: value
                    for key, value in reconciliation["per_participant"].items()
                    if key == participant_id
                },
                "production_per_participant": {
                    key: value
                    for key, value in reconciliation[
                        "production_per_participant"
                    ].items()
                    if key == participant_id
                },
                "source_evidence": {
                    **cast(Mapping[str, object], source),
                    "metadata": public_metadata,
                },
            }
        audit = cast(dict[str, object], participant_provenance["reconciliation"]).get(
            "engine_audit"
        )
        if isinstance(audit, dict):
            participant_provenance["reconciliation"] = {
                **cast(dict[str, object], participant_provenance["reconciliation"]),
                "engine_audit": {
                    key: (
                        {participant_id: value[participant_id]}
                        if participant_id in value
                        else {}
                    )
                    if key
                    in {
                        "consumption_kwh",
                        "production_kwh",
                        "allocated_kwh",
                        "credited_kwh",
                        "allocation_difference_kwh",
                        "credit_difference_kwh",
                    }
                    and isinstance(value, dict)
                    else value
                    for key, value in audit.items()
                },
            }
        participant_provenance["rounding_adjustment"] = (
            {
                "participant_id": participant_id,
                "amount_chf": _json_safe(participant_rounding["amount_chf"]),
            }
            if participant_rounding
            else None
        )
        battery_snapshot = period.get("battery_snapshot")
        battery_source = None
        if battery_snapshot is not None:
            attribution = battery_snapshot["attribution"].get(participant_id)
            if attribution is not None:
                battery_source = {
                    "battery_kwh": attribution["battery_kwh"],
                    "value_chf": attribution["value_chf"],
                }
        participant_provenance["battery_source"] = _json_safe(battery_source)
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
                "policy_snapshot": cast(dict[str, object], _json_safe(policy)),
                "provenance_snapshot": participant_provenance,
                "input_fingerprint": period["input_fingerprint"],
                "source_document_ids": _json_safe(list(source_document_ids)),
            }
        )
    return snapshots


def approve_billing_period(
    period_id: int,
    community_id: str,
    issue_date: date | None = None,
    *,
    approver_id: str | None = None,
) -> list[dict[str, object]]:
    """Issue immutable invoices for one reconciled draft period, atomically.

    The exact active community row is locked first, so invoice numbering is
    serialised across concurrent approvals of different periods of the same
    community; then the period row is locked. Every invoice is inserted with
    its frozen policy/provenance/line snapshots in one transaction, and only
    then does the period flip to ``issued``. Retrying an already issued
    period is a no-op that returns the stored invoices.

    Domain conflicts (unknown/inactive community, unknown, stale, or
    unreconciled draft) raise ``BillingApprovalError``;
    storage outages raise ``BillingStoreError`` and roll everything back.
    """
    import database

    # The legacy pool context manager supplies the DB-API transaction boundary.
    connection = cast(
        Callable[[], AbstractContextManager[BillingConnection]], database.get_connection
    )
    if issue_date is None:
        issue_date = today()
    try:
        with connection() as conn, conn.cursor() as cur:
            community_row = billing_store.lock_active_billing_community(
                cur, community_id
            )
            if not community_row:
                raise BillingApprovalError("Community is not active or does not exist")
            community_name = community_row.get("name")
            if not isinstance(community_name, str) or not community_name.strip():
                raise BillingApprovalError("Community has no valid issuer name")
            row = billing_store.lock_billing_period(cur, period_id, community_id)
            if not row:
                raise BillingApprovalError("Billing period not found")
            period = cast(Period, dict(row))
            status = period.get("status")
            if status == "issued":
                invoices = billing_store.list_period_invoices(cur, period_id)
                if not invoices:
                    raise BillingApprovalError("Issued billing period has no invoices")
                return invoices
            calculated_values = None
            if period.get("calculated_values_fingerprint"):
                source_row = billing_store.get_current_billing_source(
                    cur, community_id, period
                )
                if not source_row:
                    raise BillingApprovalError(
                        "Der VNB-Beleg dieses Entwurfs ist nicht mehr aktuell. "
                        "Erstellen Sie den Entwurf neu."
                    )
                calculated_values = dict(source_row)
                calculated_values["fingerprint"] = calculated_values[
                    "content_fingerprint"
                ]
            if status != "draft":
                raise BillingApprovalError(
                    "Only a draft billing period can be approved"
                )
            if community_row.get("require_dual_control") and (
                not period.get("prepared_by")
                or period.get("prepared_by") == approver_id
                or not approver_id
            ):
                raise BillingApprovalError(
                    "Dual-control approval requires a different recorded preparer"
                )
            period["line_items"] = billing_store.list_billing_line_items(cur, period_id)
            reconciliation = period.get("reconciliation")
            if not isinstance(reconciliation, dict):
                raise BillingApprovalError("Billing reconciliation must be an object")
            _require_original_registrations(cur, period)
            snapshots = prepare_invoice_snapshots(
                period, issue_date=issue_date, calculated_values=calculated_values
            )

            prefix = str(snapshots[0]["policy_snapshot"]["invoice_prefix"])
            sequence = _next_invoice_sequence(
                billing_store.list_invoice_numbers(
                    cur, community_id, prefix, issue_date.year
                )
            )
            for snapshot in snapshots:
                provenance_snapshot = dict(snapshot["provenance_snapshot"])
                provenance_snapshot["issuer"] = {
                    "community_id": community_id,
                    "name": community_name,
                }
                invoice_number = f"{prefix}-{issue_date.year}-{sequence:06d}"
                sequence += 1
                billing_store.insert_approved_invoice(
                    cur,
                    period_id,
                    community_id,
                    invoice_number,
                    snapshot,
                    provenance_snapshot,
                )
            billing_store.mark_billing_period_issued(cur, period_id)
            return billing_store.list_period_invoices(cur, period_id)
    except BillingApprovalError:
        raise
    except billing_store.BillingStoreError:
        raise
    except Exception as e:
        logger.error(f"[DB] Error approving billing period: {e}")
        raise billing_store.BillingStoreError("Could not approve billing period") from e


def _next_invoice_sequence(invoice_numbers: Sequence[str | None]) -> int:
    """Continue numbering after the largest existing numeric suffix."""
    sequence = 0
    for invoice_number in invoice_numbers:
        suffix = (invoice_number or "").rsplit("-", 1)[-1]
        if suffix.isdigit():
            sequence = max(sequence, int(suffix))
    return sequence + 1


def _require_original_registrations(cur: BillingCursor, period: Period) -> None:
    """Refuse issuance if any frozen participant registration has changed."""
    registrations = period["reconciliation"].get("participant_registrations")
    participants = {item["participant_id"] for item in period["line_items"]}
    if (
        not isinstance(registrations, dict)
        or set(registrations) != participants
        or any(
            not isinstance(value, str) or not value for value in registrations.values()
        )
    ):
        raise BillingApprovalError(
            "Die ursprünglichen Teilnehmerregistrierungen sind nicht belegt. "
            "Klären Sie die Zuordnung vor einer neuen Abrechnung."
        )
    actual = billing_store.lock_billing_registrations(cur, participants)
    if actual != registrations:
        raise BillingApprovalError(
            "Eine Teilnehmerregistrierung hat sich seit der Vorbereitung geändert. "
            "Klären Sie die Zuordnung und erstellen Sie den Entwurf neu."
        )
