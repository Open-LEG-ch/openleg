# SPDX-License-Identifier: AGPL-3.0-or-later
"""Adapter from SDAT metering readings to billing engine frames.

``billing_engine.generate_billing_summary`` wants two pandas frames with an
identical interval index and one column per participant. ``store.metering``
holds point-keyed rows in UTC. This module is the only place that translation
happens, which makes it the right place for the data-quality gate: a period
that cannot be billed fails here, naming the offending metering point, instead
of producing a plausible but wrong invoice.

Two deliberate choices:

* Participants are keyed on ``building_id``, not on the metering point. A member
  with a separate production meter is one participant with two points, and
  ``billing_line_items.participant_id`` already speaks building ids.
* The engine reallocates production itself. The VNB already allocated in the
  E66 file (the ``community_kwh`` channel), so the two numbers can disagree.
  ``reconcile_with_vnb`` reports that gap rather than hiding it.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta
from zoneinfo import ZoneInfo

import pandas as pd

import database as db

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "Europe/Zurich"
RESOLUTION_MINUTES = 15
CONFIRMED_STATUS = "confirmed"

_MAX_REPORTED = 5


class PeriodDataError(ValueError):
    """A period cannot be billed. ``problems`` lists every defect found."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__(_describe(self.problems))


@dataclass(frozen=True)
class PeriodFrames:
    """Engine-ready frames plus the provenance needed to audit them."""

    consumption: pd.DataFrame
    production: pd.DataFrame
    participants: tuple
    vnb_reference: dict
    provenance: dict


def _describe(problems):
    by_kind = {}
    for problem in problems:
        by_kind.setdefault(problem["kind"], []).append(problem["detail"])
    parts = []
    for kind, details in sorted(by_kind.items()):
        shown = details[:_MAX_REPORTED]
        suffix = (
            f" (+{len(details) - len(shown)} more)" if len(details) > len(shown) else ""
        )
        parts.append(f"{kind}: {'; '.join(shown)}{suffix}")
    return " | ".join(parts)


def _problem(kind, detail, metering_point_id=None):
    return {"kind": kind, "detail": detail, "metering_point_id": metering_point_id}


def _localise(moment, tzinfo):
    """Naive boundaries are read in the period timezone, not in UTC."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=tzinfo)
    return moment


def _expected_index(period_start, period_end, tzinfo):
    return pd.date_range(
        start=period_start,
        end=period_end - timedelta(minutes=RESOLUTION_MINUTES),
        freq=f"{RESOLUTION_MINUTES}min",
        tz=tzinfo,
    )


def _participant_map(points, problems):
    """metering_point_id -> building_id, rejecting anything unbillable."""
    mapping = {}
    for point in points:
        point_id = point.get("metering_point_id")
        building_id = point.get("building_id")
        status = point.get("member_status")
        if not building_id:
            problems.append(
                _problem(
                    "unmapped_point",
                    f"{point_id} is not assigned to a building",
                    point_id,
                )
            )
            continue
        if status != CONFIRMED_STATUS:
            problems.append(
                _problem(
                    "unconfirmed_member",
                    f"{point_id} belongs to building {building_id} "
                    f"with membership status {status or 'missing'}",
                    point_id,
                )
            )
            continue
        mapping[point_id] = building_id
    return mapping


def _check_rows(readings, mapping, expected_index, problems):
    """Reject unknown points, mixed resolutions, negatives and duplicates."""
    seen = set()
    grid = {moment.to_pydatetime() for moment in expected_index}
    for row in readings:
        point_id = row.get("metering_point_id")
        direction = row.get("direction")
        measured_at = row.get("measured_at")

        if point_id not in mapping:
            problems.append(
                _problem(
                    "unknown_point",
                    f"{point_id} has readings but is not a billable point of "
                    "this community",
                    point_id,
                )
            )
            continue

        resolution = row.get("resolution_minutes")
        if resolution != RESOLUTION_MINUTES:
            problems.append(
                _problem(
                    "mixed_resolution",
                    f"{point_id} at {measured_at} has resolution {resolution}, "
                    f"expected {RESOLUTION_MINUTES}",
                    point_id,
                )
            )

        for channel in ("total_kwh", "grid_kwh", "community_kwh"):
            value = row.get(channel)
            if value is not None and float(value) < 0:
                problems.append(
                    _problem(
                        "negative_value",
                        f"{point_id} at {measured_at} has {channel}={value}",
                        point_id,
                    )
                )
        if direction not in {"consumption", "production"}:
            problems.append(
                _problem(
                    "unknown_direction",
                    f"{point_id} at {measured_at} has direction {direction!r}, "
                    "which is neither consumption nor production",
                    point_id,
                )
            )

        if (
            direction in {"consumption", "production"}
            and row.get("community_kwh") is None
        ):
            problems.append(
                _problem(
                    "missing_vnb_allocation",
                    f"{point_id} ({direction}) at {measured_at} has no community_kwh",
                    point_id,
                )
            )

        if measured_at not in grid:
            # Assigning this by label would extend the frame with an interval
            # nobody expects, and the period would still look complete.
            problems.append(
                _problem(
                    "misaligned_interval",
                    f"{point_id} ({direction}) at {measured_at} is not on the "
                    f"{RESOLUTION_MINUTES}-minute grid of this period",
                    point_id,
                )
            )
            continue

        key = (point_id, direction, measured_at)
        if key in seen:
            problems.append(
                _problem(
                    "duplicate_interval",
                    f"{point_id} ({direction}) appears twice at {measured_at}",
                    point_id,
                )
            )
        seen.add(key)

    _check_gaps(seen, mapping, expected_index, problems)


def _check_gaps(seen, mapping, expected_index, problems):
    """Every series present in the period must cover every interval.

    A series that is absent altogether is not a gap: a member without a
    production meter simply has no production series.
    """
    series = {(point_id, direction) for point_id, direction, _ in seen}
    for point_id, direction in sorted(series):
        missing = [
            moment
            for moment in expected_index
            if (point_id, direction, moment.to_pydatetime()) not in seen
        ]
        if missing:
            shown = ", ".join(
                m.strftime("%Y-%m-%d %H:%M") for m in missing[:_MAX_REPORTED]
            )
            problems.append(
                _problem(
                    "missing_interval",
                    f"{point_id} ({direction}) is missing {len(missing)} "
                    f"interval(s): {shown}",
                    point_id,
                )
            )


def _empty_frame(index, participants):
    return pd.DataFrame(0.0, index=index, columns=list(participants))


def load_period_frames(
    community_id, period_start, period_end, *, timezone=DEFAULT_TIMEZONE
):
    """Load one billable period as engine-ready frames.

    The interval is half-open: ``[period_start, period_end)``. Naive boundaries
    are interpreted in ``timezone``. Raises :class:`PeriodDataError` listing
    every defect rather than the first one, so an operator can fix a period in
    one pass.
    """
    tzinfo = ZoneInfo(timezone)
    period_start = _localise(period_start, tzinfo)
    period_end = _localise(period_end, tzinfo)
    if period_end <= period_start:
        raise PeriodDataError(
            [_problem("empty_period", f"{period_end} is not after {period_start}")]
        )

    problems = []
    points = db.get_community_metering_points(community_id)
    mapping = _participant_map(points, problems)
    readings = db.get_period_readings(community_id, period_start, period_end)

    if not readings:
        problems.append(
            _problem(
                "no_readings",
                f"no readings for community {community_id} between "
                f"{period_start.isoformat()} and {period_end.isoformat()}",
            )
        )
        raise PeriodDataError(problems)

    index = _expected_index(period_start, period_end, tzinfo)
    _check_rows(readings, mapping, index, problems)
    if problems:
        raise PeriodDataError(problems)

    participants = tuple(sorted(set(mapping.values())))
    frames = {
        "consumption": _empty_frame(index, participants),
        "production": _empty_frame(index, participants),
    }
    vnb_totals = {
        "consumption": dict.fromkeys(participants, 0.0),
        "production": dict.fromkeys(participants, 0.0),
    }
    documents = set()

    for row in readings:
        participant = mapping[row["metering_point_id"]]
        direction = row["direction"]
        frame = frames.get(direction)
        if frame is None:
            # Unreachable in practice: _check_rows reports an unrecognised
            # direction and load_period_frames raises before this loop runs.
            continue
        moment = pd.Timestamp(row["measured_at"]).tz_convert(tzinfo)
        total = float(row.get("total_kwh") or 0.0)
        frame.loc[moment, participant] += total
        community = row.get("community_kwh")
        if community is not None:
            vnb_totals[direction][participant] += float(community)
        if row.get("source_document_id"):
            documents.add(row["source_document_id"])

    if problems:
        raise PeriodDataError(problems)

    vnb_reference = {
        "community_consumption_kwh": round(sum(vnb_totals["consumption"].values()), 6),
        "community_production_kwh": round(sum(vnb_totals["production"].values()), 6),
        "per_participant": {
            participant: {
                "consumption_kwh": round(vnb_totals["consumption"][participant], 6),
                "production_kwh": round(vnb_totals["production"][participant], 6),
            }
            for participant in participants
        },
    }
    provenance = {
        "source_document_ids": tuple(sorted(documents)),
        "interval_count": len(index),
        "resolution_minutes": RESOLUTION_MINUTES,
        "period_start": period_start,
        "period_end": period_end,
        "timezone": timezone,
    }

    return PeriodFrames(
        consumption=frames["consumption"].astype(float),
        production=frames["production"].astype(float),
        participants=participants,
        vnb_reference=vnb_reference,
        provenance=provenance,
    )


def reconcile_with_vnb(frames, summary):
    """Compare the engine's allocation with the allocation the VNB delivered.

    The E66 ``community_kwh`` channel is the VNB's own split between grid supply
    and community supply. ``allocate_energy`` derives its own split from total
    production and consumption. Both are defensible, they are not equal, and the
    difference is what a member will ask about, so it gets reported.
    """
    vnb_allocated = float(frames.vnb_reference["community_consumption_kwh"])
    charges = [
        item
        for item in summary.get("line_items", [])
        if item.get("item_type") == "consumer_charge"
    ]
    engine_allocated = (
        sum(float(item.get("quantity_kwh") or 0.0) for item in charges)
        if charges
        else float(summary.get("total_allocated_kwh") or 0.0)
    )
    charged_by_participant = {
        item["participant_id"]: float(item.get("quantity_kwh") or 0.0)
        for item in charges
    }
    production_credits = {
        item["participant_id"]: float(item.get("quantity_kwh") or 0.0)
        for item in summary.get("line_items", [])
        if item.get("item_type") == "producer_credit"
    }

    per_participant = {}
    for item in summary.get("participants", []):
        participant = item["id"]
        reference = frames.vnb_reference["per_participant"].get(participant, {})
        vnb_kwh = float(reference.get("consumption_kwh") or 0.0)
        engine_kwh = charged_by_participant.get(
            participant, float(item.get("allocated_kwh") or 0.0)
        )
        per_participant[participant] = {
            "vnb_kwh": round(vnb_kwh, 6),
            "engine_kwh": round(engine_kwh, 6),
            "difference_kwh": round(engine_kwh - vnb_kwh, 6),
        }

    difference = engine_allocated - vnb_allocated
    vnb_production = float(frames.vnb_reference["community_production_kwh"])
    engine_production = sum(production_credits.values())
    production_per_participant = {}
    for participant, reference in frames.vnb_reference["per_participant"].items():
        vnb_kwh = float(reference.get("production_kwh") or 0.0)
        engine_kwh = production_credits.get(participant, 0.0)
        production_per_participant[participant] = {
            "vnb_kwh": round(vnb_kwh, 6),
            "engine_kwh": round(engine_kwh, 6),
            "difference_kwh": round(engine_kwh - vnb_kwh, 6),
        }
    return {
        "vnb_allocated_kwh": round(vnb_allocated, 6),
        "engine_allocated_kwh": round(engine_allocated, 6),
        "difference_kwh": round(difference, 6),
        "difference_pct": round(difference / vnb_allocated * 100, 4)
        if vnb_allocated
        else None,
        "per_participant": per_participant,
        "vnb_production_kwh": round(vnb_production, 6),
        "engine_production_kwh": round(engine_production, 6),
        "production_difference_kwh": round(engine_production - vnb_production, 6),
        "production_per_participant": production_per_participant,
    }
