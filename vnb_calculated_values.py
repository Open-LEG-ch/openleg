# SPDX-License-Identifier: AGPL-3.0-or-later
"""Versioned exchange contract for VNB-calculated quarter-hour LEG values.

The public interface deliberately accepts every transport after it has produced
the same Python mapping. Validation, normalization, replay protection and safe
diagnostics therefore have one implementation.
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CONTRACT_VERSION = "vnb-calculated-values/1"
FORMAT_VERSIONS = ("json/1",)
TRANSPORTS = ("api", "file", "manual")
UNITS = ("kWh", "Wh")
RESOLUTION = timedelta(minutes=15)


class CalculatedValuesError(ValueError):
    """The requested VNB evidence is unavailable or unusable."""


def exchange_capabilities():
    return {
        "calculated_values": {
            "contract_version": CONTRACT_VERSION,
            "format_versions": list(FORMAT_VERSIONS),
            "transports": list(TRANSPORTS),
            "units": list(UNITS),
        }
    }


def _parse_moment(value, code, diagnostics):
    try:
        moment = datetime.fromisoformat(value)
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError
        return moment
    except (TypeError, ValueError):
        diagnostics.append({"code": code})
        return None


def _fingerprint(payload, normalized_records):
    identity = {
        key: payload.get(key)
        for key in (
            "contract_version",
            "format_version",
            "territory",
            "community_id",
            "period_start",
            "period_end",
            "timezone",
            "source",
            "vnb_case_id",
        )
    }
    identity["records"] = sorted(
        normalized_records,
        key=lambda row: json.dumps(row, sort_keys=True, separators=(",", ":")),
    )
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _safe_result(saved):
    return {key: value for key, value in saved.items() if key != "evidence_bytes"}


def accept_delivery(payload, *, transport, evidence, repository=None):
    """Validate and persist a delivery, returning no raw source material."""
    if repository is None:
        import database as repository

    diagnostics = []
    if transport not in TRANSPORTS:
        diagnostics.append({"code": "unsupported_transport"})
    if payload.get("contract_version") != CONTRACT_VERSION:
        diagnostics.append({"code": "unsupported_contract"})
    if payload.get("format_version") not in FORMAT_VERSIONS:
        diagnostics.append({"code": "unsupported_format"})
    if not payload.get("source") or not payload.get("vnb_case_id"):
        diagnostics.append({"code": "missing_provenance"})
    unit = payload.get("unit")
    if unit not in UNITS:
        diagnostics.append({"code": "unsupported_unit"})

    community_id = payload.get("community_id")
    community = repository.get_calculated_values_community(community_id)
    if not community:
        diagnostics.append({"code": "unknown_community"})
    elif community.get("territory") != payload.get("territory"):
        diagnostics.append({"code": "tenant_mismatch"})

    start = _parse_moment(payload.get("period_start"), "invalid_period", diagnostics)
    end = _parse_moment(payload.get("period_end"), "invalid_period", diagnostics)
    try:
        local_zone = ZoneInfo(payload.get("timezone"))
    except (TypeError, ZoneInfoNotFoundError):
        diagnostics.append({"code": "invalid_timezone"})
        local_zone = None
    if start and end:
        aligned = all(
            moment.minute % 15 == 0 and moment.second == 0 and moment.microsecond == 0
            for moment in (start, end)
        )
        if end <= start or not aligned:
            diagnostics.append({"code": "invalid_period"})

    normalized = []
    seen = set()
    record_problem = False
    for record in payload.get("records") or []:
        if not isinstance(record, dict):
            diagnostics.append({"code": "invalid_interval"})
            record_problem = True
            continue
        moment = _parse_moment(
            record.get("measured_at"), "invalid_interval", diagnostics
        )
        direction = record.get("direction")
        participant = record.get("participant_id")
        try:
            value = Decimal(str(record.get("value")))
        except (InvalidOperation, TypeError):
            value = None
        if (
            not moment
            or not participant
            or direction not in {"consumption", "production"}
            or value is None
            or not value.is_finite()
            or value < 0
            or (start and end and not (start <= moment < end))
            or moment.minute % 15
            or moment.second
            or moment.microsecond
        ):
            if moment:
                diagnostics.append({"code": "invalid_interval"})
            record_problem = True
            continue
        key = (moment.astimezone(timezone.utc), participant, direction)
        if key in seen:
            diagnostics.append({"code": "overlapping_interval"})
            record_problem = True
            continue
        seen.add(key)
        kwh = value / (Decimal(1000) if unit == "Wh" else Decimal(1))
        normalized.append(
            {
                "measured_at": key[0].isoformat(),
                "participant_id": participant,
                "direction": direction,
                "allocated_kwh": float(kwh),
            }
        )

    if start and end and local_zone and not record_problem:
        expected_count = int(
            (end.astimezone(timezone.utc) - start.astimezone(timezone.utc)) / RESOLUTION
        )
        series = {(row["participant_id"], row["direction"]) for row in normalized}
        expected_series = {
            (item["participant_id"], item["direction"])
            for item in (community or {}).get("expected_series", [])
        }
        if (
            series != expected_series
            or not series
            or any(
                sum(
                    row["participant_id"] == participant
                    and row["direction"] == direction
                    for row in normalized
                )
                != expected_count
                for participant, direction in series
            )
        ):
            diagnostics.append({"code": "incomplete_period"})
            record_problem = True

    # A stable fallback still makes malformed retries idempotent.
    fingerprint = _fingerprint(payload, normalized)
    if (
        start
        and end
        and community
        and not diagnostics
        and any(
            item["fingerprint"] != fingerprint
            for item in repository.find_overlapping_calculated_values(
                payload.get("territory"), community_id, start, end
            )
        )
    ):
        diagnostics.append({"code": "overlapping_period"})
    metadata_invalid = bool(diagnostics) and not record_problem
    status = (
        "partially_invalid"
        if record_problem
        else "rejected"
        if metadata_invalid
        else "accepted"
    )
    persisted = {
        "contract_version": payload.get("contract_version"),
        "format_version": payload.get("format_version"),
        "transport": transport,
        "territory": payload.get("territory"),
        "community_id": community_id,
        "period_start": start,
        "period_end": end,
        "timezone": payload.get("timezone"),
        "unit": unit,
        "source": payload.get("source"),
        "vnb_case_id": payload.get("vnb_case_id"),
        "fingerprint": fingerprint,
        "evidence_sha256": hashlib.sha256(bytes(evidence)).hexdigest(),
        "evidence_bytes": bytes(evidence),
        "status": status,
        "diagnostics": diagnostics,
        "normalized_records": normalized,
        "record_count": len(normalized),
    }
    return _safe_result(repository.save_calculated_values_delivery(persisted))


def load_billing_reference(community_id, period_start, period_end, *, repository=None):
    """Return totals from accepted evidence or fail the billing period closed."""
    if repository is None:
        import database as repository

    delivery = repository.get_validated_calculated_values(
        community_id, period_start, period_end
    )
    if not delivery:
        raise CalculatedValuesError("Billing period has no validated VNB evidence")
    per_participant = {}
    totals = {"consumption": 0.0, "production": 0.0}
    for row in delivery["normalized_records"]:
        participant = per_participant.setdefault(
            row["participant_id"], {"consumption_kwh": 0.0, "production_kwh": 0.0}
        )
        key = f"{row['direction']}_kwh"
        participant[key] += row["allocated_kwh"]
        totals[row["direction"]] += row["allocated_kwh"]
    return {
        "community_consumption_kwh": round(totals["consumption"], 6),
        "community_production_kwh": round(totals["production"], 6),
        "per_participant": {
            key: {field: round(value, 6) for field, value in values.items()}
            for key, values in per_participant.items()
        },
        "evidence_fingerprint": delivery["fingerprint"],
        "vnb_case_id": delivery["vnb_case_id"],
        "source": delivery["source"],
    }
