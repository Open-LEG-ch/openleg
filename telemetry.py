# SPDX-License-Identifier: AGPL-3.0-or-later
"""Strict operational observations. This contract has no billing conversion."""

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

VERSION = "telemetry/1"
MAX_BYTES = 65536
MAX_BATCH = 100
RATE_PER_MINUTE = 60
IDENTIFIER = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
UNITS = {
    "power": "W",
    "energy_counter": "Wh",
    "interval_energy": "Wh",
    "state_of_charge": "%",
}
LOCATIONS = {
    "pv_inverter": {"generation"},
    "grid_connection": {"import", "export"},
    "battery": {"charge", "discharge", "stored"},
    "site": {"consumption", "generation"},
}


class TelemetryError(ValueError):
    def __init__(self, code="invalid_telemetry", status=400):
        super().__init__(code)
        self.code = code
        self.status = status


def identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise TelemetryError()
    return value


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise TelemetryError()
    return value


def retention(payload):
    return {
        "raw_retention_days": integer(payload.get("raw_retention_days", 7), 1, 7),
        "aggregate_retention_days": integer(
            payload.get("aggregate_retention_days", 90), 1, 90
        ),
    }


def enrollment(payload):
    required = {"name", "device_id", "source_id", "cadence_seconds"}
    if (
        not isinstance(payload, dict)
        or not required <= payload.keys()
        or payload.keys()
        - required
        - {"raw_retention_days", "aggregate_retention_days"}
    ):
        raise TelemetryError()
    name = payload["name"]
    if (
        not isinstance(name, str)
        or not name.strip()
        or len(name) > 80
        or any(ord(c) < 32 for c in name)
    ):
        raise TelemetryError()
    return {
        "name": name.strip(),
        "device_id": identifier(payload["device_id"]),
        "source_id": identifier(payload["source_id"]),
        "cadence_seconds": integer(payload["cadence_seconds"], 1, 86400),
        **retention(payload),
    }


def timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        raise TelemetryError()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise TelemetryError() from None


def observations(payload, installation, now):
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "samples"}
        or payload["schema_version"] != VERSION
    ):
        raise TelemetryError()
    samples = payload["samples"]
    if not isinstance(samples, list) or not 1 <= len(samples) <= MAX_BATCH:
        raise TelemetryError()
    result = []
    previous = 0
    for sample in samples:
        row = observation(sample, installation, now)
        if row["sequence"] <= previous:
            raise TelemetryError("unordered_sequence", 409)
        previous = row["sequence"]
        result.append(row)
    return result


def observation(sample, installation, now):
    required = {
        "sample_id",
        "sequence",
        "device_id",
        "source_id",
        "cadence_seconds",
        "metric",
        "unit",
        "value",
        "direction",
        "measurement_location",
        "quality",
        "observed_at",
    }
    if (
        not isinstance(sample, dict)
        or not required <= sample.keys()
        or sample.keys() - required - {"interval_start", "interval_end"}
    ):
        raise TelemetryError()
    row = dict(sample)
    identifier(row["sample_id"])
    integer(row["sequence"], 1, 2**63 - 1)
    integer(row["cadence_seconds"], 1, 86400)
    if any(
        row[key] != installation[key]
        for key in ("device_id", "source_id", "cadence_seconds")
    ):
        raise TelemetryError("source_mismatch", 403)
    metric, unit = row["metric"], row["unit"]
    if not isinstance(metric, str) or UNITS.get(metric) != unit:
        raise TelemetryError()
    location, direction = row["measurement_location"], row["direction"]
    if (
        not isinstance(location, str)
        or not isinstance(direction, str)
        or direction not in LOCATIONS.get(location, set())
    ):
        raise TelemetryError()
    if (metric == "state_of_charge") != (direction == "stored"):
        raise TelemetryError()
    if row["quality"] not in ("measured", "estimated", "unknown"):
        raise TelemetryError()
    try:
        if type(row["value"]) not in (str, int, float) or len(str(row["value"])) > 40:
            raise ValueError()
        value = Decimal(str(row["value"]))
        if (
            not value.is_finite()
            or not 0 <= value <= (100 if metric == "state_of_charge" else 10**12)
            or value.as_tuple().exponent < -9
        ):
            raise ValueError()
    except (InvalidOperation, ValueError):
        raise TelemetryError() from None
    row["value"] = format(value, "f")
    observed = timestamp(row["observed_at"]) if row["observed_at"] is not None else None
    cutoff = now - timedelta(days=installation["raw_retention_days"])
    if observed is not None and not cutoff <= observed <= now + timedelta(minutes=5):
        raise TelemetryError("observation_outside_window")
    row["observed_at"] = observed.isoformat() if observed else None
    if metric == "interval_energy":
        start, end = (
            timestamp(row.get("interval_start")),
            timestamp(row.get("interval_end")),
        )
        if (
            observed != end
            or not cutoff <= start < end
            or end - start > timedelta(days=1)
        ):
            raise TelemetryError()
        row.update(interval_start=start.isoformat(), interval_end=end.isoformat())
    elif "interval_start" in row or "interval_end" in row:
        raise TelemetryError()
    row["fingerprint"] = hashlib.sha256(
        json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return row


def freshness(row, now):
    observed = row["observed_at"]
    if observed is None or row["quality"] == "unknown":
        return "unknown"
    age = (now - timestamp(observed)).total_seconds()
    if age < 0:
        return "clock_skew"
    return "fresh" if age <= max(60, row["cadence_seconds"] * 2) else "stale"
