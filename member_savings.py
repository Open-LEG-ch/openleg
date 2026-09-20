# SPDX-License-Identifier: AGPL-3.0-or-later
"""Display-ready read model for one member's realized LEG savings.

A member sees what their LEG membership actually returned for a billed
period, computed from their own E66 metering readings and the frozen policy
snapshot the invoice used:

* consumption in kWh: the sum of the consumption series' total_kwh over the
  billed half-open window,
* the locally covered share: the same series' community_kwh, the VNB's own
  metered split between grid supply and community supply,
* the realized saving in CHF: the network discount the engine prices for
  locally covered energy (billing_engine.compute_network_discount) at the
  grid fee and network level frozen in the policy snapshot.

Nothing here re-derives a tariff at render time. The grid fee and the
network level come from the invoice's stored policy_snapshot, so the section
restates exactly the tariff the invoice's own Netzentgelt line shows. The
pre-formation estimate calculator keeps working on typed-in consumption;
this module never consults it.

A period whose consumption series does not cover every 15-minute interval
of the billed window exactly once renders the explicit empty state instead
of a guessed or stale figure. Malformed snapshots or readings fail closed
with MemberSavingsDataError rather than rendering an invented number.

The view supports exactly one complete consumption series per building: a
building whose consumption arrives over several metering points is reported
as incomplete rather than silently summed.
"""

import json
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

import billing_engine
import billing_policy
import billing_workspace
import database as db

_INTERVAL = timedelta(minutes=15)

EMPTY_STATE_MESSAGE = (
    "Für diese Periode liegen keine vollständigen Messwerte vor. "
    "Die realisierte Ersparnis kann erst gezeigt werden, wenn die "
    "VNB-Messdaten vollständig übermittelt wurden."
)


class MemberSavingsDataError(RuntimeError):
    """Stored billing or metering data is malformed and cannot be shown."""


def _decimal_text(value: Decimal, places: int, scale: Decimal = Decimal(1)) -> str:
    """Format an already-validated finite decimal with half-up rounding."""
    quantum = Decimal(1).scaleb(-places)
    return format((value * scale).quantize(quantum, rounding=ROUND_HALF_UP), "f")


def _require_finite_decimal(value, message: str) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        raise MemberSavingsDataError(message) from None
    if not amount.is_finite():
        raise MemberSavingsDataError(message)
    return amount


def _require_json_dict(value, message: str) -> dict:
    """Decode a PostgreSQL JSONB value that may already be a dict, or a JSON
    string; fail closed on anything malformed, missing, or empty."""
    decoded = value
    if isinstance(decoded, str):
        try:
            decoded = json.loads(decoded)
        except (TypeError, ValueError):
            raise MemberSavingsDataError(message) from None
    if not isinstance(decoded, dict) or not decoded:
        raise MemberSavingsDataError(message)
    return decoded


def _require_positive_id(value, message: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise MemberSavingsDataError(message)
    return value


def _require_text(value, message: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MemberSavingsDataError(message)
    return value


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _period_bounds(provenance: dict) -> tuple[datetime, datetime]:
    """Parse the frozen half-open window; naive bounds are read as UTC."""
    message = "Die Rechnung hat keine gültige Periodenangabe."
    bounds = []
    for key in ("period_start", "period_end"):
        moment = provenance.get(key)
        if isinstance(moment, datetime):
            parsed = moment
        elif isinstance(moment, str) and moment.strip():
            try:
                parsed = datetime.fromisoformat(moment)
            except ValueError:
                raise MemberSavingsDataError(message) from None
        else:
            raise MemberSavingsDataError(message)
        bounds.append(_as_utc(parsed))
    start, end = bounds
    if end <= start:
        raise MemberSavingsDataError(message)
    return start, end


def _policy_view(policy: dict) -> tuple[Decimal, str]:
    """Validate the frozen grid fee and network level of the policy snapshot."""
    grid_fee = _require_finite_decimal(
        policy.get("grid_fee_chf_per_kwh"),
        "Die Richtlinien-Kopie hat ein ungültiges Netzentgelt.",
    )
    if grid_fee < 0:
        raise MemberSavingsDataError(
            "Die Richtlinien-Kopie hat ein ungültiges Netzentgelt."
        )
    network_level = policy.get("network_level")
    if network_level not in billing_policy.NETWORK_LEVELS:
        raise MemberSavingsDataError(
            "Die Richtlinien-Kopie hat eine ungültige Netzebene."
        )
    return grid_fee, network_level


def _consumption_rows(readings, start: datetime, end: datetime) -> list[dict]:
    """The building's consumption rows inside the half-open billed window."""
    rows = []
    for row in readings:
        if not isinstance(row, dict):
            raise MemberSavingsDataError(EMPTY_STATE_MESSAGE)
        moment = row.get("measured_at")
        if not isinstance(moment, datetime):
            raise MemberSavingsDataError(EMPTY_STATE_MESSAGE)
        if start <= _as_utc(moment) < end and row.get("direction") == "consumption":
            rows.append(row)
    return rows


def _require_complete_series(rows, start: datetime, end: datetime) -> None:
    """Fail closed unless consumption covers every interval exactly once."""
    elapsed = end - start
    if elapsed.total_seconds() % _INTERVAL.total_seconds() != 0:
        raise MemberSavingsDataError(
            "Die Periode passt nicht auf das 15-Minuten-Messraster."
        )
    interval_count = int(elapsed / _INTERVAL)
    if interval_count <= 0 or len(rows) != interval_count:
        raise MemberSavingsDataError(EMPTY_STATE_MESSAGE)
    seen = {_as_utc(row["measured_at"]) for row in rows}
    if len(seen) != interval_count:
        raise MemberSavingsDataError(EMPTY_STATE_MESSAGE)


def _require_channel(row: dict, key: str, total: Decimal | None) -> Decimal:
    value = _require_finite_decimal(
        row.get(key), "Ein Messwert dieser Periode ist ungültig."
    )
    if value < 0 or (total is not None and value > total):
        raise MemberSavingsDataError("Ein Messwert dieser Periode ist ungültig.")
    return value


def _empty_state(period_label: str) -> dict:
    return {
        "available": False,
        "period_label": period_label,
        "message": EMPTY_STATE_MESSAGE,
    }


def realized_savings(readings, provenance: dict, policy: dict) -> dict:
    """Build the realized-savings view from metered readings and the frozen policy.

    ``readings`` are the participant's own E66 rows as the metering store
    returns them (Decimal money channels, UTC ``measured_at``).
    ``provenance`` is the invoice's frozen provenance snapshot and ``policy``
    its frozen policy snapshot. Returns the empty state, not a guess, when
    the consumption series does not cover the billed window exactly once.

    The period label is the invoice's own: derived from the frozen period
    start in its original timezone, never from the UTC-normalized window.
    """
    if not isinstance(policy, dict) or not policy:
        raise MemberSavingsDataError(
            "Die Rechnung hat keine gültige Richtlinien-Kopie."
        )
    grid_fee, network_level = _policy_view(policy)
    provenance = _require_json_dict(
        provenance, "Die Rechnung hat keine gültige Periodenangabe."
    )
    start, end = _period_bounds(provenance)
    rows = _consumption_rows(readings, start, end)
    period_label = billing_workspace.period_label(provenance.get("period_start"))
    try:
        _require_complete_series(rows, start, end)
    except MemberSavingsDataError as exc:
        if str(exc) != EMPTY_STATE_MESSAGE:
            raise
        return _empty_state(period_label)

    consumption_kwh = Decimal(0)
    local_kwh = Decimal(0)
    for row in rows:
        total = _require_channel(row, "total_kwh", None)
        consumption_kwh += total
        local_kwh += _require_channel(row, "community_kwh", total)

    share_pct = (
        (local_kwh / consumption_kwh * Decimal(100)).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        )
        if consumption_kwh > 0
        else None
    )
    try:
        savings_chf = _require_finite_decimal(
            billing_engine.compute_network_discount(
                float(local_kwh), float(grid_fee), network_level
            ),
            "Die Ersparnis dieser Periode ist nicht berechenbar.",
        )
    except (ArithmeticError, TypeError, ValueError) as exc:
        raise MemberSavingsDataError(
            "Die Ersparnis dieser Periode ist nicht berechenbar."
        ) from exc
    return {
        "available": True,
        "period_label": period_label,
        "consumption_kwh": consumption_kwh,
        "local_kwh": local_kwh,
        "local_share_pct": share_pct,
        "savings_chf": savings_chf,
        "display_consumption_kwh": _decimal_text(consumption_kwh, 3),
        "display_local_kwh": _decimal_text(local_kwh, 3),
        "display_local_share_pct": (
            _decimal_text(share_pct, 1) if share_pct is not None else None
        ),
        "display_savings_chf": _decimal_text(savings_chf, 2),
        "display_grid_fee_rp": _decimal_text(grid_fee, 2, Decimal(100)),
        "network_level_label": billing_policy.NETWORK_LEVEL_LABELS[network_level],
    }


def invoice_savings_view(invoice_id: int, building_id: str) -> dict | None:
    """Realized savings for one own invoice, or None for a missing id.

    The owner-scoped store read is the access gate: a wrong id and another
    participant's id are indistinguishable, exactly like the invoice detail
    view. Readings are fetched for the caller's building_id only, so a
    member can never see another building's metered numbers.
    """
    invoice_id = _require_positive_id(invoice_id, "Die Rechnung hat keine gültige ID.")
    building_id = _require_text(building_id, "Die Rechnung hat keine gültige Zuordnung.")
    invoice = db.get_invoice_for_participant(invoice_id, building_id)
    if not invoice:
        return None
    participant_id = _require_text(
        invoice.get("participant_id"), "Die Rechnung hat keine gültige Teilnehmer-ID."
    )
    if participant_id != building_id:
        raise MemberSavingsDataError("Die Rechnung hat eine ungültige Zuordnung.")
    provenance = _require_json_dict(
        invoice.get("provenance_snapshot"),
        "Die Rechnung hat keine gültige Periodenangabe.",
    )
    start, end = _period_bounds(provenance)
    try:
        readings = db.get_building_period_readings(building_id, start, end)
    except Exception as exc:
        raise MemberSavingsDataError(
            "Die Messwerte dieser Periode sind unvollständig."
        ) from exc
    return realized_savings(readings, provenance, invoice.get("policy_snapshot"))
