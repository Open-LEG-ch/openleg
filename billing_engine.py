# SPDX-License-Identifier: AGPL-3.0-or-later
"""Billing engine for LEG 15-minute interval energy allocation.

Implements Art. 17d/17e StromVG allocation models:
- Proportional: by consumption share
- Einfach (equal): equal split, capped by actual consumption
- Network discount: 40% same level, 20% cross level
"""

from decimal import ROUND_HALF_UP, Decimal
from math import floor, isfinite

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


def _battery_source_attribution(
    production, consumption, allocation, participant_id, capacity_kwh
):
    """Attribute already-allocated energy to one metered battery source."""
    try:
        capacity_kwh = float(capacity_kwh)
    except (TypeError, ValueError) as exc:
        raise ValueError("Battery capacity must be finite and positive") from exc
    if not isfinite(capacity_kwh) or capacity_kwh <= 0:
        raise ValueError("Battery capacity must be finite and positive")
    if (
        participant_id not in production.columns
        or participant_id not in consumption.columns
    ):
        raise ValueError("The configured battery has incomplete metering readings")

    discharge = production[participant_id]
    charge = consumption[participant_id]
    if ((charge > 0) & (discharge > 0)).any():
        raise ValueError("The configured battery charges and discharges simultaneously")

    # An unknown opening state is valid when some opening state in [0, capacity]
    # can explain the whole measured series. The cumulative range proves that.
    state_delta = (charge - discharge).cumsum()
    state_path = np.concatenate(([0.0], state_delta.to_numpy(dtype=float)))
    state_span = float(state_path.max() - state_path.min())
    if state_span > capacity_kwh + 1e-9:
        raise ValueError("The configured battery readings exceed its capacity")

    total_production = production.sum(axis=1)
    battery_fraction = (
        discharge.div(total_production.where(total_production > 0))
        .fillna(0.0)
        .clip(lower=0.0, upper=1.0)
    )
    allocated_by_consumer = {}
    raw_battery_by_consumer = {}
    for consumer_id in allocation.columns:
        if consumer_id == participant_id:
            continue
        allocated = round(float(allocation[consumer_id].sum()), 6)
        battery_kwh = float((allocation[consumer_id] * battery_fraction).sum())
        allocated_by_consumer[consumer_id] = allocated
        raw_battery_by_consumer[consumer_id] = min(battery_kwh, allocated)

    # Persisted line quantities use six decimals. Floor each source share and
    # distribute the remaining micro-kWh deterministically so the participant
    # attribution sums exactly to the battery producer credit.
    battery_by_consumer = {
        consumer_id: floor(value * 1_000_000) / 1_000_000
        for consumer_id, value in raw_battery_by_consumer.items()
    }
    battery_target = round(sum(raw_battery_by_consumer.values()), 6)
    remaining_units = round(
        (battery_target - sum(battery_by_consumer.values())) * 1_000_000
    )
    remainder_order = sorted(
        raw_battery_by_consumer,
        key=lambda consumer_id: (
            -(
                raw_battery_by_consumer[consumer_id] * 1_000_000
                - floor(raw_battery_by_consumer[consumer_id] * 1_000_000)
            ),
            str(consumer_id),
        ),
    )
    for consumer_id in remainder_order[:remaining_units]:
        battery_by_consumer[consumer_id] = round(
            battery_by_consumer[consumer_id] + 0.000001, 6
        )

    attribution = {}
    for consumer_id, allocated in allocated_by_consumer.items():
        battery_kwh = battery_by_consumer[consumer_id]
        attribution[consumer_id] = {
            "direct_solar_kwh": round(allocated - battery_kwh, 6),
            "battery_kwh": battery_kwh,
        }

    charged_kwh = round(float(charge.sum()), 6)
    discharged_kwh = round(float(discharge.sum()), 6)
    audit = {
        "charged_kwh": charged_kwh,
        "discharged_kwh": discharged_kwh,
        "charge_discharge_difference_kwh": round(charged_kwh - discharged_kwh, 6),
        "allocated_battery_kwh": battery_target,
    }
    audit["unallocated_discharge_kwh"] = round(
        discharged_kwh - audit["allocated_battery_kwh"], 6
    )
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
    battery_capacity_kwh=None,
):
    """Generate billing summary for a period.

    Args:
        production: pd.DataFrame with one column per producer (kWh)
        consumption: pd.DataFrame with one column per consumer (kWh)
        grid_fee_per_kwh: Grid usage fee per kWh (CHF)
        internal_price_per_kwh: Internal price per kWh (CHF)
        network_level: "same" (40% discount) or "cross" (20% discount)
        distribution_model: "proportional" or "einfach"
        settlement_fee_per_kwh: VNB settlement fee per settled kWh (CHF).
            Zero leaves the draft in the exact pre-fee shape so periods
            billed without the fee stay reproducible.
        battery_participant_id: participant whose production is battery
            discharge and whose consumption is battery charging.
        battery_capacity_kwh: configured usable capacity. The measured state
            swing must fit inside it.

    Returns:
        dict with total_production_kwh, total_allocated_kwh,
        total_network_discount_chf, participants (list of per-participant summaries)
    """
    try:
        grid_fee_per_kwh = float(grid_fee_per_kwh)
        internal_price_per_kwh = float(internal_price_per_kwh)
        settlement_fee_per_kwh = float(settlement_fee_per_kwh)
    except (TypeError, ValueError) as exc:
        raise ValueError("Billing prices must be finite and non-negative") from exc
    if not all(
        isfinite(price) and price >= 0
        for price in (grid_fee_per_kwh, internal_price_per_kwh, settlement_fee_per_kwh)
    ):
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
        if producer_production is None:
            raise ValueError("Battery allocation requires participant production")
        battery_attribution, battery_audit = _battery_source_attribution(
            producer_production,
            consumption,
            allocation,
            battery_participant_id,
            battery_capacity_kwh,
        )

    total_production = float(total_production_series.sum())
    total_allocated = float(allocation.values.sum())
    total_discount = compute_network_discount(
        total_allocated, grid_fee_per_kwh, network_level
    )

    participants = []
    line_items = []
    charge_fee_total = Decimal(0)
    for col in allocation.columns:
        alloc_kwh = float(allocation[col].sum())
        priced_quantity = round(alloc_kwh, 6)
        cons_kwh = float(consumption[col].sum())
        discount = compute_network_discount(alloc_kwh, grid_fee_per_kwh, network_level)
        cost = _priced_amount(priced_quantity, internal_price_per_kwh)
        participant = {
            "id": col,
            "consumption_kwh": round(cons_kwh, 2),
            "allocated_kwh": round(alloc_kwh, 2),
            "self_supply_ratio": round(alloc_kwh / cons_kwh, 4) if cons_kwh > 0 else 0,
            "internal_cost_chf": _currency(cost),
            "network_discount_chf": _currency(_money(discount)),
        }
        if battery_attribution is not None and col != battery_participant_id:
            participant.update(battery_attribution[col])
            participant["battery_value_chf"] = _currency(
                _priced_amount(participant["battery_kwh"], internal_price_per_kwh)
            )
        participants.append(participant)
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
            if settlement_fee_per_kwh > 0:
                fee_amount = _priced_amount(priced_quantity, settlement_fee_per_kwh)
                charge_fee_total += fee_amount
                participant["settlement_fee_chf"] = _currency(fee_amount)
                line_items.append(
                    {
                        "participant_id": col,
                        "item_type": "settlement_fee",
                        "quantity_kwh": priced_quantity,
                        "unit_price_chf_per_kwh": settlement_fee_per_kwh,
                        "amount_chf": float(fee_amount),
                    }
                )

    if producer_production is not None:
        allocated_by_interval = allocation.sum(axis=1)
        shares = producer_production.div(
            total_production_series.replace(0, float("nan")), axis=0
        ).fillna(0)
        credited = shares.mul(allocated_by_interval, axis=0).sum(axis=0)
        charge_total = sum(
            _money(item["amount_chf"])
            for item in line_items
            if item["item_type"] == "consumer_charge"
        )
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

    summary = {
        "total_production_kwh": round(total_production, 2),
        "total_allocated_kwh": round(total_allocated, 2),
        "total_surplus_kwh": round(max(0, total_production - total_allocated), 2),
        "total_network_discount_chf": _currency(_money(total_discount)),
        "internal_price_chf_per_kwh": internal_price_per_kwh,
        "grid_fee_chf_per_kwh": grid_fee_per_kwh,
        "distribution_model": distribution_model,
        "network_level": network_level,
        "participants": participants,
        "line_items": line_items,
    }
    if settlement_fee_per_kwh > 0:
        summary["settlement_fee_chf_per_kwh"] = settlement_fee_per_kwh
        summary["total_settlement_fee_chf"] = _currency(charge_fee_total)
    if battery_audit is not None:
        summary["battery_energy"] = battery_audit
    return summary
