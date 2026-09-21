# SPDX-License-Identifier: AGPL-3.0-or-later
"""Billing engine for LEG 15-minute interval energy allocation.

Implements Art. 17d/17e StromVG allocation models:
- Proportional: by consumption share
- Einfach (equal): equal split, capped by actual consumption
- Network discount: 40% same level, 20% cross level
"""

from decimal import ROUND_HALF_UP, Decimal
from math import isfinite

import numpy as np
import pandas as pd

DISCOUNT_SAME_LEVEL = 0.40
DISCOUNT_CROSS_LEVEL = 0.20


def _money(value):
    return Decimal(str(value)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def _priced_amount(quantity, unit_price):
    return _money(Decimal(str(quantity)) * Decimal(str(unit_price)))


def _currency(value):
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def allocate_energy(production, consumption, model="proportional"):
    """Allocate solar production to consumers per 15-min interval.

    Args:
        production: pd.Series of production values per interval (kWh)
        consumption: pd.DataFrame with one column per consumer (kWh)
        model: "proportional" or "einfach"

    Returns:
        pd.DataFrame with same columns as consumption, values = allocated kWh
    """
    result = pd.DataFrame(0.0, index=consumption.index, columns=consumption.columns)

    for i in range(len(production)):
        prod = production.iloc[i]
        if prod <= 0:
            continue

        cons = consumption.iloc[i]
        total_cons = cons.sum()

        if total_cons <= 0:
            continue

        if model == "proportional":
            available = min(prod, total_cons)
            shares = cons / total_cons
            allocated = shares * available
            # Cap at actual consumption
            allocated = allocated.clip(upper=cons)
            result.iloc[i] = allocated

        elif model == "einfach":
            n = len(cons)
            equal_share = prod / n
            remaining = prod

            # First pass: allocate equal share, capped by consumption
            alloc = cons.clip(upper=equal_share)
            remaining -= alloc.sum()

            # Second pass: distribute remainder to those who can absorb
            if remaining > 0.001:
                unfilled = (cons - alloc).clip(lower=0)
                unfilled_total = unfilled.sum()
                if unfilled_total > 0:
                    extra = unfilled / unfilled_total * remaining
                    extra = extra.clip(upper=unfilled)
                    alloc = alloc + extra

            result.iloc[i] = alloc

    return result


def compute_network_discount(allocated_kwh, grid_fee_per_kwh, network_level):
    """Compute Netznutzungsentgelt discount for LEG allocation.

    Args:
        allocated_kwh: Total allocated energy in kWh
        grid_fee_per_kwh: Grid usage fee per kWh (CHF)
        network_level: "same" (40% discount) or "cross" (20% discount)

    Returns:
        Discount amount in CHF
    """
    if allocated_kwh <= 0:
        return 0.0

    rate = DISCOUNT_SAME_LEVEL if network_level == "same" else DISCOUNT_CROSS_LEVEL
    return allocated_kwh * grid_fee_per_kwh * rate


def battery_source_attribution(
    production, consumption, allocation, battery_participant_id
):
    """Split each participant's allocation into direct solar and battery energy.

    The battery's discharge is the production column of its own Messpunkt;
    its charging is the consumption column of the same point. Allocation
    itself is unchanged: every production column, battery included, feeds
    the same single-phase allocation the VNB reconciliation checks against.
    The split below only labels each participant's allocated energy by
    source, interval by interval, so quantities and money stay exactly as
    the VNB reconciliation saw them.

    ``allocation`` is the allocation ``generate_billing_summary`` already
    computed over the total production series; this function never
    re-allocates.

    Returns ``(attribution, energy_audit)``: the attribution maps each
    consumer participant to the battery-sourced kWh of their allocation;
    the audit carries ``charged_kwh``, ``discharged_kwh``, and
    ``losses_kwh`` for the period (a negative loss means the battery left
    the period fuller than it started).
    """
    if not hasattr(production, "columns") or battery_participant_id not in getattr(
        production, "columns", []
    ):
        raise ValueError(
            "The configured battery metering point has no production "
            "readings in this period"
        )
    # A battery that only discharges has no consumption column in the
    # period; its charging reads as zero then.
    charged_kwh = (
        float(consumption[battery_participant_id].sum())
        if battery_participant_id in consumption.columns
        else 0.0
    )

    battery_production = production[battery_participant_id]
    total_production = production.sum(axis=1)
    battery_fraction = (
        (battery_production / total_production)
        .where(total_production > 0, 0.0)
        .fillna(0.0)
    )

    attribution = {}
    battery_kwh_total = 0.0
    for col in allocation.columns:
        if col == battery_participant_id:
            continue
        battery_kwh = float((allocation[col] * battery_fraction).sum())
        battery_kwh_total += battery_kwh
        attribution[col] = round(battery_kwh, 6)

    discharged_kwh = float(battery_production.sum())
    audit = {
        "charged_kwh": round(charged_kwh, 6),
        "discharged_kwh": round(discharged_kwh, 6),
        "losses_kwh": round(charged_kwh - discharged_kwh, 6),
        "sourced_battery_kwh": round(battery_kwh_total, 6),
    }
    return attribution, audit


def generate_billing_summary(
    production,
    consumption,
    grid_fee_per_kwh,
    internal_price_per_kwh,
    network_level,
    distribution_model="proportional",
    settlement_fee_per_kwh=0.0,
    battery_participant_id=None,
):
    """Generate billing summary for a period.

    Args:
        settlement_fee_per_kwh: VNB settlement fee in CHF per kWh of energy
            allocated inside the community; defaults to 0 (no fee).
        battery_participant_id: the Messpunkt of the shared battery, whose
            production column is its discharge and whose consumption column
            is its charging. Attribution of direct vs battery energy is only
            computed when the point is given.

    Returns:
        dict with total_production_kwh, total_allocated_kwh,
        total_network_discount_chf, total_settlement_fee_chf,
        participants (list of per-participant summaries), and, with a
        battery, the ``battery_energy`` audit block
    """
    try:
        grid_fee_per_kwh = float(grid_fee_per_kwh)
        internal_price_per_kwh = float(internal_price_per_kwh)
        settlement_fee_per_kwh = float(settlement_fee_per_kwh)
    except (TypeError, ValueError) as exc:
        raise ValueError("Billing prices must be finite and non-negative") from exc
    if not all(
        isfinite(price) and price >= 0
        for price in (grid_fee_per_kwh, internal_price_per_kwh)
    ):
        raise ValueError("Billing prices must be finite and non-negative")
    if not isfinite(settlement_fee_per_kwh) or settlement_fee_per_kwh < 0:
        raise ValueError("Billing prices must be finite and non-negative")
    if network_level not in {"same", "cross"}:
        raise ValueError("network_level must be 'same' or 'cross'")
    if distribution_model not in {"proportional", "einfach"}:
        raise ValueError("Unsupported distribution model")
    try:
        production = production.astype(float)
        consumption = consumption.astype(float)
    except (TypeError, ValueError) as exc:
        raise ValueError("Billing readings must be finite and non-negative") from exc
    if (
        not np.isfinite(production.to_numpy()).all()
        or not np.isfinite(consumption.to_numpy()).all()
        or (production < 0).to_numpy().any()
        or (consumption < 0).to_numpy().any()
    ):
        raise ValueError("Billing readings must be finite and non-negative")

    producer_production = production if isinstance(production, pd.DataFrame) else None
    total_production_series = (
        production.sum(axis=1) if producer_production is not None else production
    )
    if not total_production_series.index.equals(consumption.index):
        raise ValueError("Production and consumption intervals must match")

    allocation = allocate_energy(
        total_production_series, consumption, model=distribution_model
    )

    battery_attribution = None
    battery_audit = None
    if battery_participant_id is not None:
        battery_attribution, battery_audit = battery_source_attribution(
            production, consumption, allocation, battery_participant_id
        )

    total_production = float(total_production_series.sum())
    total_allocated = float(allocation.values.sum())
    total_discount = compute_network_discount(
        total_allocated, grid_fee_per_kwh, network_level
    )
    total_settlement_fee = _money(total_allocated * settlement_fee_per_kwh)

    participants = []
    line_items = []
    for col in allocation.columns:
        alloc_kwh = float(allocation[col].sum())
        priced_quantity = round(alloc_kwh, 6)
        cons_kwh = float(consumption[col].sum())
        discount = compute_network_discount(alloc_kwh, grid_fee_per_kwh, network_level)
        cost = _priced_amount(priced_quantity, internal_price_per_kwh)
        settlement_fee = _money(alloc_kwh * settlement_fee_per_kwh)

        participants.append(
            {
                "id": col,
                "consumption_kwh": round(cons_kwh, 2),
                "allocated_kwh": round(alloc_kwh, 2),
                "self_supply_ratio": round(alloc_kwh / cons_kwh, 4)
                if cons_kwh > 0
                else 0,
                "internal_cost_chf": _currency(cost),
                "network_discount_chf": _currency(_money(discount)),
                "settlement_fee_chf": _currency(settlement_fee),
                **(
                    {"battery_kwh": battery_attribution.get(col, 0.0)}
                    if battery_attribution is not None and col != battery_participant_id
                    else {}
                ),
            }
        )
        if producer_production is not None:
            line_items.append(
                {
                    "participant_id": col,
                    "item_type": "consumer_charge",
                    "quantity_kwh": priced_quantity,
                    "unit_price_chf_per_kwh": internal_price_per_kwh,
                    "amount_chf": float(
                        _priced_amount(priced_quantity, internal_price_per_kwh)
                    ),
                }
            )

    if producer_production is not None:
        allocated_by_interval = allocation.sum(axis=1)
        shares = producer_production.div(
            total_production_series.replace(0, float("nan")), axis=0
        ).fillna(0)
        credited = shares.mul(allocated_by_interval, axis=0).sum(axis=0)
        charge_total = sum(_money(item["amount_chf"]) for item in line_items)
        credited_total = Decimal(0)
        producer_ids = list(credited.index)
        for producer_id in producer_ids:
            quantity = round(float(credited[producer_id]), 6)
            amount = _priced_amount(quantity, internal_price_per_kwh)
            credited_total += amount
            line_items.append(
                {
                    "participant_id": producer_id,
                    "item_type": "producer_credit",
                    "quantity_kwh": quantity,
                    "unit_price_chf_per_kwh": internal_price_per_kwh,
                    "amount_chf": -float(amount),
                }
            )

        rounding_difference = charge_total - credited_total
        if producer_ids and rounding_difference:
            line_items.append(
                {
                    "participant_id": min(producer_ids, key=str),
                    "item_type": "rounding_adjustment",
                    "quantity_kwh": None,
                    "unit_price_chf_per_kwh": None,
                    "amount_chf": -float(rounding_difference),
                }
            )

    return {
        "total_production_kwh": round(total_production, 2),
        "total_allocated_kwh": round(total_allocated, 2),
        "total_surplus_kwh": round(max(0, total_production - total_allocated), 2),
        "total_network_discount_chf": _currency(_money(total_discount)),
        "total_settlement_fee_chf": _currency(total_settlement_fee),
        "internal_price_chf_per_kwh": internal_price_per_kwh,
        "grid_fee_chf_per_kwh": grid_fee_per_kwh,
        "settlement_fee_chf_per_kwh": settlement_fee_per_kwh,
        "distribution_model": distribution_model,
        "network_level": network_level,
        "participants": participants,
        "line_items": line_items,
        **({"battery_energy": battery_audit} if battery_audit is not None else {}),
    }
