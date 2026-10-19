# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exact compatibility checks for independently rounded E66 LEG channels.

A certificate proves that one proportional allocation can explain every source
channel's rounding cell. It does not reconstruct the VNB's unrounded values.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from fractions import Fraction

VERSION = "vnb-proportional-e66-rounding/1"
_HALF_QUANTUM = Fraction(1, 2000)
_INTERVAL = timedelta(minutes=15)
_MAX_STEPS = 128


class ReconciliationError(ValueError):
    """A fixed diagnostic explaining why the source cannot support billing."""


@dataclass(frozen=True)
class _Cell:
    lower: Fraction
    upper: Fraction


@dataclass(frozen=True)
class _Channels:
    total: _Cell
    local: _Cell
    residual: _Cell


def _cell(value):
    number = Decimal(str(value))
    if not number.is_finite() or number < 0 or number * 1000 % 1:
        raise ReconciliationError("invalid_vnb_precision")
    exact = Fraction(number)
    return _Cell(max(Fraction(0), exact - _HALF_QUANTUM), exact + _HALF_QUANTUM)


def _moment(value):
    result = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ReconciliationError("incomplete_vnb_source")
    result = result.astimezone(UTC)
    if result.minute % 15 or result.second or result.microsecond:
        raise ReconciliationError("incomplete_vnb_source")
    return result


def _group_source(delivery):
    metadata = delivery["source_metadata"]
    if (
        delivery["status"] != "accepted"
        or delivery.get("lifecycle_state", "current") != "current"
        or delivery["format_version"] != "e66/1"
        or metadata["precision"] != "0.001 kWh"
    ):
        raise ReconciliationError("unsupported_vnb_precision_profile")
    start, end = (_moment(delivery[key]) for key in ("period_start", "period_end"))
    if end <= start:
        raise ReconciliationError("incomplete_vnb_source")
    expected = set()
    points = set()
    for mapping in metadata["mappings"]:
        point = mapping["metering_point_id"]
        if point in points or not mapping["building_id"]:
            raise ReconciliationError("incomplete_vnb_source")
        points.add(point)
        for direction in mapping["expected_directions"]:
            if direction not in {"consumption", "production"}:
                raise ReconciliationError("incomplete_vnb_source")
            expected.add((point, mapping["building_id"], direction))
    if not expected:
        raise ReconciliationError("incomplete_vnb_source")
    groups = {}
    identities = {}
    for record in delivery["normalized_records"]:
        moment = _moment(record["measured_at"])
        identity = (
            record["source_metering_point_id"],
            record["participant_id"],
            record["direction"],
        )
        seen = identities.setdefault(moment, set())
        if (
            moment < start
            or moment >= end
            or identity not in expected
            or identity in seen
        ):
            raise ReconciliationError("incomplete_vnb_source")
        seen.add(identity)
        values = record["source_quantities"]
        if Decimal(str(record["allocated_kwh"])) != Decimal(values["community_kwh"]):
            raise ReconciliationError("inconsistent_vnb_source")
        channels = _Channels(
            _cell(values["total_kwh"]),
            _cell(values["community_kwh"]),
            _cell(values["grid_kwh"]),
        )
        group = groups.setdefault(moment, {"consumption": [], "production": []})
        group[record["direction"]].append(channels)
    if len(groups) != int((end - start) / _INTERVAL) or any(
        seen != expected for seen in identities.values()
    ):
        raise ReconciliationError("incomplete_vnb_source")
    return groups


def _intersection(first, second):
    lower, upper = max(first.lower, second.lower), min(first.upper, second.upper)
    return _Cell(lower, upper) if lower < upper else None


def _zero_allocation(fully_used, unused):
    return all(
        row.total.lower == row.local.lower == row.residual.lower == 0
        for row in fully_used
    ) and all(
        row.local.lower == 0 and _intersection(row.total, row.residual) is not None
        for row in unused
    )


def _fully_used(rows):
    intersections = []
    for row in rows:
        if row.residual.lower != 0:
            return None
        intersection = _intersection(row.total, row.local)
        if intersection is None:
            return None
        intersections.append(intersection)
    if not intersections:
        return None
    return _Cell(
        sum((cell.lower for cell in intersections), Fraction(0)),
        sum((cell.upper for cell in intersections), Fraction(0)),
    )


def _ratio_bounds(rows):
    lower, upper = Fraction(0), Fraction(1)
    for row in rows:
        total, local, residual = row.total, row.local, row.residual
        lower = max(
            lower,
            local.lower / total.upper,
            local.lower / (local.lower + residual.upper),
        )
        upper = min(
            upper,
            1 - residual.lower / total.upper,
            local.upper / (residual.lower + local.upper),
        )
        if total.lower:
            lower = max(lower, 1 - residual.upper / total.lower)
            upper = min(upper, local.upper / total.lower)
    return lower, upper


def _partial_allocation(fully_used, partially_used):
    available = _fully_used(fully_used)
    if available is None or not partially_used:
        return None
    lower, upper = _ratio_bounds(partially_used)
    if lower >= upper:
        return None
    # All cell endpoints are rational and all inequalities are strict. An
    # accepted midpoint is an exact witness, including both residual channels
    # and conservation. Exhausting the search always refuses the interval.
    for _ in range(_MAX_STEPS):
        ratio = (lower + upper) / 2
        residual_ratio = ratio / (1 - ratio)
        allocated_lower = Fraction(0)
        allocated_upper = Fraction(0)
        for row in partially_used:
            allocated_lower += max(
                ratio * row.total.lower,
                row.local.lower,
                residual_ratio * row.residual.lower,
            )
            allocated_upper += min(
                ratio * row.total.upper,
                row.local.upper,
                residual_ratio * row.residual.upper,
            )
        if allocated_lower >= available.upper:
            upper = ratio
        elif allocated_upper <= available.lower:
            lower = ratio
        else:
            return ratio
    raise ReconciliationError("unresolved_vnb_rounding_boundary")


def _interval_witness(group):
    consumption, production = group["consumption"], group["production"]
    for side, fully_used, partly_used in (
        ("production", production, consumption),
        ("consumption", consumption, production),
    ):
        if _zero_allocation(fully_used, partly_used):
            return {"limiting_side": side, "ratio": "0"}
    consumer_total, producer_total = _fully_used(consumption), _fully_used(production)
    if (
        consumer_total
        and producer_total
        and _intersection(consumer_total, producer_total)
    ):
        return {"limiting_side": "both", "ratio": "1"}
    unresolved = False
    for side, fully_used, partly_used in (
        ("production", production, consumption),
        ("consumption", consumption, production),
    ):
        try:
            ratio = _partial_allocation(fully_used, partly_used)
        except ReconciliationError:
            unresolved = True
            continue
        if ratio is not None:
            return {"limiting_side": side, "ratio": str(ratio)}
    raise ReconciliationError(
        "unresolved_vnb_rounding_boundary" if unresolved else "allocation_disagreement"
    )


def audit_e66(delivery, *, model):
    """Return a checkable interval certificate or refuse the billing evidence."""
    if model != "proportional":
        raise ReconciliationError("unsupported_vnb_allocation_model")
    try:
        groups = _group_source(delivery)
        intervals = [
            {"measured_at": moment.isoformat(), **_interval_witness(group)}
            for moment, group in sorted(groups.items())
        ]
        certificate = {
            "version": VERSION,
            "outcome": "rounding_compatible",
            "model": model,
            "source_precision_kwh": "0.001",
            "rounding": "nonnegative_half_up",
            "source_fingerprint": delivery["fingerprint"],
            "interval_count": len(intervals),
            "intervals": intervals,
        }
        certificate["certificate_sha256"] = hashlib.sha256(
            json.dumps(certificate, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return certificate
    except ReconciliationError:
        raise
    except (KeyError, TypeError, ValueError, ArithmeticError, InvalidOperation):
        raise ReconciliationError("incomplete_vnb_source") from None
