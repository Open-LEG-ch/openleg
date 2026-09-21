# SPDX-License-Identifier: AGPL-3.0-or-later
"""Validation and draft assembly for the shared storage asset (Quartierakku).

One battery per community. Its annual cost is split across the participants
by percentage; the percentages must cover every billed participant and sum
to exactly 100. Domain logic only, no SQL. Every invalid configuration is
refused: OpenLEG never guesses money-path inputs.
"""

from decimal import ROUND_HALF_UP, Decimal

_MAX_NAME_LENGTH = 80
_MAX_CAPACITY_KWH = Decimal(100000)
_MAX_ANNUAL_COST_CHF = Decimal(100000)
_SHARE_TOTAL = Decimal(100)

_CENT = Decimal("0.01")


class BatteryAssetError(ValueError):
    """A storage asset configuration is incomplete or outside its domain."""


def _decimal(value):
    try:
        return Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        return Decimal("NaN")


def validate_battery_form(form, participants) -> dict:
    """Validate one storage-asset form submission.

    Returns ``{"asset": {...}, "errors": {}}`` on success, otherwise
    ``{"asset": None, "errors": {field: message}}``. The share inputs are
    keyed by ``share_<participant_id>`` and must cover every listed
    participant.
    """
    errors = {}
    name = (form.get("name") or "").strip()
    if not name or len(name) > _MAX_NAME_LENGTH:
        errors["name"] = "Bezeichnung mit 1 bis 80 Zeichen eingeben."

    participant_id = (form.get("participant_id") or "").strip()
    if not participant_id or len(participant_id) > 64:
        errors["participant_id"] = (
            "Teilnehmer des Quartierakkus angeben: das Gebäude, über dessen "
            "Messpunkt der Speicher angeschlossen ist."
        )

    capacity = _decimal((form.get("capacity_kwh") or "").strip())
    if not capacity.is_finite() or capacity <= 0 or capacity > _MAX_CAPACITY_KWH:
        errors["capacity_kwh"] = (
            "Speicherkapazität in kWh, grösser als 0 und höchstens 100000."
        )

    annual_cost = _decimal((form.get("annual_cost_chf") or "").strip())
    if (
        not annual_cost.is_finite()
        or annual_cost < 0
        or annual_cost > _MAX_ANNUAL_COST_CHF
    ):
        errors["annual_cost_chf"] = "Jährliche Kosten in CHF, zwischen 0 und 100000."

    participant_ids = [
        str(participant).strip() for participant in list(participants or [])
    ]
    participant_ids = [participant for participant in participant_ids if participant]
    if not participant_ids:
        errors["shares"] = "Für die Kostenaufteilung mindestens ein Teilnehmer angeben."

    shares = []
    if participant_ids:
        share_total = Decimal(0)
        for member_id in participant_ids:
            raw = (form.get(f"share_{member_id}") or "").strip()
            share = _decimal(raw)
            if not share.is_finite() or share < 0:
                errors[f"share_{member_id}"] = (
                    f"Anteil für {member_id} in Prozent, zwischen 0 und 100."
                )
                continue
            shares.append((member_id, share))
            share_total += share
        if not errors and share_total != _SHARE_TOTAL:
            errors["shares"] = (
                "Die Anteile müssen genau 100 Prozent ergeben, aktuell "
                f"{share_total.quantize(Decimal('0.01'))} Prozent."
            )

    if errors:
        return {"asset": None, "errors": errors}
    return {
        "asset": {
            "name": name,
            "participant_id": participant_id,
            "capacity_kwh": capacity,
            "annual_cost_chf": annual_cost,
            "shares": shares,
        },
        "errors": {},
    }


def draft_block(asset, participants) -> dict:
    """Build the frozen battery block a billing period is computed with.

    Fails closed with :class:`BatteryAssetError` when the stored shares do
    not cover exactly the billed participants or do not sum to 100. Each
    participant's cost-share amount is the share percentage of the annual
    cost, at cent precision with half-up rounding.
    """
    if not isinstance(asset, dict) or not asset:
        raise BatteryAssetError(
            "Der Quartierakku ist konfiguriert, aber unvollständig."
        )
    name = asset.get("name")
    participant_id = str(asset.get("participant_id") or "").strip()
    capacity = _decimal(asset.get("capacity_kwh"))
    annual_cost = _decimal(asset.get("annual_cost_chf"))
    if not name or not participant_id:
        raise BatteryAssetError(
            "Der Quartierakku ist konfiguriert, aber unvollständig."
        )
    if not capacity.is_finite() or capacity <= 0:
        raise BatteryAssetError(
            "Der Quartierakku ist konfiguriert, aber unvollständig."
        )
    if not annual_cost.is_finite() or annual_cost < 0:
        raise BatteryAssetError(
            "Der Quartierakku ist konfiguriert, aber unvollständig."
        )

    share_map = {}
    share_total = Decimal(0)
    for member_id, share_pct in dict(asset.get("shares") or {}).items():
        member_id = str(member_id)
        share_pct = _decimal(share_pct)
        if not share_pct.is_finite() or share_pct < 0:
            raise BatteryAssetError("Die Anteile des Quartierakkus sind ungültig.")
        share_map[member_id] = share_pct
        share_total += share_pct

    # Cost shares belong to the human members; the battery's own participant
    # carries no share of the asset.
    billed = [
        str(participant)
        for participant in list(participants or [])
        if str(participant) != participant_id
    ]
    missing = sorted(set(billed) - set(share_map))
    if missing:
        raise BatteryAssetError(
            "Der Quartierakku hat keinen Anteil für: " + ", ".join(missing)
        )
    if share_total != _SHARE_TOTAL:
        raise BatteryAssetError(
            "Die Anteile des Quartierakkus ergeben nicht 100 Prozent."
        )

    participant_amounts = {
        member_id: (share_pct * annual_cost / Decimal(100)).quantize(
            _CENT, rounding=ROUND_HALF_UP
        )
        for member_id, share_pct in share_map.items()
    }
    return {
        "name": name,
        "participant_id": participant_id,
        "capacity_kwh": str(capacity),
        "annual_cost_chf": str(annual_cost),
        "shares_pct": {
            member_id: str(share_pct) for member_id, share_pct in share_map.items()
        },
        "share_amounts_chf": {
            member_id: str(amount) for member_id, amount in participant_amounts.items()
        },
    }


def validate_frozen_block(block, billed_participants) -> dict:
    """Fail closed on a persisted battery block; return it unchanged.

    Approval freezes exactly this shape into every invoice snapshot, so the
    check mirrors ``draft_block``: the block must be complete, its shares
    must cover every billed participant, the percentages must sum to 100,
    and every stored cost-share amount must equal its percentage of the
    annual cost at cent precision.
    """
    if not isinstance(block, dict) or not block:
        raise BatteryAssetError("Der Quartierakku der Periode ist unvollständig.")
    name = block.get("name")
    capacity = _decimal(block.get("capacity_kwh"))
    annual_cost = _decimal(block.get("annual_cost_chf"))
    if not name or not capacity.is_finite() or capacity <= 0:
        raise BatteryAssetError("Der Quartierakku der Periode ist unvollständig.")
    if not annual_cost.is_finite() or annual_cost < 0:
        raise BatteryAssetError("Der Quartierakku der Periode ist unvollständig.")

    share_map = {}
    share_total = Decimal(0)
    raw_shares = block.get("shares_pct")
    if isinstance(raw_shares, dict):
        items = list(raw_shares.items())
    elif isinstance(raw_shares, (list, tuple)):
        items = [(pair[0], pair[1]) for pair in raw_shares]
    else:
        raise BatteryAssetError(
            "Der Quartierakku der Periode hat keine gültigen Anteile."
        )
    for member_id, share_pct in items:
        member_id = str(member_id)
        share_pct = _decimal(share_pct)
        if not share_pct.is_finite() or share_pct < 0:
            raise BatteryAssetError("Die Anteile des Quartierakkus sind ungültig.")
        share_map[member_id] = share_pct
        share_total += share_pct
    # Cost shares belong to the human members; the battery's own participant
    # carries no share of the asset.
    battery_participant = str(block.get("participant_id") or "").strip()
    billed = [
        str(participant)
        for participant in list(billed_participants or [])
        if str(participant) != battery_participant
    ]
    missing = sorted(set(billed) - set(share_map))
    if missing:
        raise BatteryAssetError(
            "Der Quartierakku der Periode hat keinen Anteil für: " + ", ".join(missing)
        )
    if share_total != _SHARE_TOTAL:
        raise BatteryAssetError(
            "Die Anteile des Quartierakkus der Periode ergeben nicht 100 Prozent."
        )

    # A battery wired to its own Messpunkt must carry the per-participant
    # sourcing attribution the engine computed; a cost-share-only battery
    # has none.
    attribution = block.get("attribution_kwh")
    if block.get("participant_id") and attribution is not None:
        if not isinstance(attribution, dict):
            raise BatteryAssetError(
                "Der Quartierakku der Periode hat keine Quellen-Aufteilung."
            )
        for member_id in billed:
            kwh = _decimal(attribution.get(member_id))
            if not kwh.is_finite() or kwh < 0:
                raise BatteryAssetError(
                    "Die Quellen-Aufteilung des Quartierakkus ist ungültig."
                )

    amounts = block.get("share_amounts_chf")
    if not isinstance(amounts, dict):
        raise BatteryAssetError(
            "Der Quartierakku der Periode hat keine Kostenaufteilung."
        )
    for participant_id, share_pct in share_map.items():
        stored = _decimal(amounts.get(participant_id))
        expected = (share_pct * annual_cost / Decimal(100)).quantize(
            _CENT, rounding=ROUND_HALF_UP
        )
        if not stored.is_finite() or stored != expected:
            raise BatteryAssetError(
                "Die Kostenaufteilung des Quartierakkus stimmt nicht mit "
                "den Anteilen überein."
            )
    return block


def participant_share(block: dict, participant_id: str) -> dict | None:
    """One participant's frozen cost-share entry, or None without a battery.

    The battery's own participant owns no share and gets no entry; a human
    participant without a share is an inconsistent period and fails closed.
    """
    if not block:
        return None
    if participant_id == str(block.get("participant_id") or ""):
        return None
    share_pct = block.get("shares_pct", {}).get(participant_id)
    amount = block.get("share_amounts_chf", {}).get(participant_id)
    if share_pct is None or amount is None:
        raise BatteryAssetError(
            "Der Quartierakku der Periode hat keinen Anteil für diesen Teilnehmer."
        )
    entry = {
        "name": block.get("name"),
        "capacity_kwh": block.get("capacity_kwh"),
        "annual_cost_chf": block.get("annual_cost_chf"),
        "share_pct": share_pct,
        "share_amount_chf": amount,
    }
    attribution = block.get("attribution_kwh") or {}
    if attribution:
        entry["battery_kwh"] = str(_decimal(attribution.get(participant_id, 0)))
    return entry
