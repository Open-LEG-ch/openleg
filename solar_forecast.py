# SPDX-License-Identifier: AGPL-3.0-or-later
"""MeteoSwiss point/radiation contracts, separate from household measurements."""

import csv
import io
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

SWISS_TIME = ZoneInfo("Europe/Zurich")
SOURCE_PREFIX = "https://data.geo.admin.ch/ch.meteoschweiz.ogd-local-forecasting/"
MAX_CSV_BYTES = 34 * 1024 * 1024
MAX_ROWS = 2_000_000
MAX_POINT_DISTANCE_KM = 20
RELEASE_MAX_AGE = timedelta(hours=3)


class ForecastError(ValueError):
    """An unsupported or malformed public forecast must remain unavailable."""


@dataclass(frozen=True, slots=True)
class Point:
    point_type_id: int
    point_id: int
    name: str
    latitude: float
    longitude: float


@dataclass(frozen=True, slots=True)
class LocatedPoint:
    point: Point
    distance_km: float


@dataclass(frozen=True, slots=True)
class Release:
    released_at: datetime
    source_url: str
    sha256: str


@dataclass(frozen=True, slots=True)
class Radiation:
    starts_at: datetime
    ends_at: datetime
    radiation_w_m2: Decimal


def select_release(items: dict, now: datetime) -> Release | None:
    """Select a parameter release, not the enclosing day's STAC timestamp."""
    candidates = []
    features = items.get("features", [])
    if not isinstance(features, list) or len(features) > 10:
        raise ForecastError("invalid_forecast_items")
    for feature in features:
        assets = feature.get("assets", {})
        if not isinstance(assets, dict) or len(assets) > 4096:
            raise ForecastError("invalid_forecast_assets")
        for name, asset in assets.items():
            match = re.fullmatch(r"vnut12\.lssw\.(\d{12})\.gre000h0\.csv", name)
            if match is None or not isinstance(asset, dict):
                continue
            stamp = match[1]
            try:
                released_at = datetime.strptime(stamp, "%Y%m%d%H%M").replace(tzinfo=UTC)
            except ValueError:
                continue
            expected_url = f"{SOURCE_PREFIX}{stamp[:8]}-ch/{name}"
            checksum = asset.get("file:checksum", "")
            if (
                released_at > now
                or released_at.minute != 0
                or asset.get("href") != expected_url
                or not isinstance(checksum, str)
                or not re.fullmatch(r"1220[0-9a-f]{64}", checksum)
            ):
                continue
            candidates.append(Release(released_at, expected_url, checksum[4:]))
    return max(candidates, key=lambda release: release.released_at, default=None)


def _rows(data: bytes, required: set[str]):
    if len(data) > MAX_CSV_BYTES:
        raise ForecastError("forecast_download_too_large")
    reader = csv.DictReader(
        io.TextIOWrapper(io.BytesIO(data), encoding="latin-1"), delimiter=";"
    )
    if not required.issubset(reader.fieldnames or []):
        raise ForecastError("invalid_forecast_columns")
    for index, row in enumerate(reader):
        if (
            index >= MAX_ROWS
            or None in row
            or any(row[key] is None for key in required)
        ):
            raise ForecastError("invalid_forecast_rows")
        yield row


def _identity(row) -> tuple[int, int]:
    try:
        kind, identifier = int(row["point_type_id"]), int(row["point_id"])
    except (ValueError, TypeError):
        raise ForecastError("invalid_point_identity") from None
    if kind not in (1, 2, 3) or not 1 <= identifier <= 10**10:
        raise ForecastError("invalid_point_identity")
    return kind, identifier


def parse_points(data: bytes) -> list[Point]:
    points: dict[tuple[int, int], Point] = {}
    required = {
        "point_id",
        "point_type_id",
        "point_name",
        "point_coordinates_wgs84_lat",
        "point_coordinates_wgs84_lon",
    }
    for row in _rows(data, required):
        identity = _identity(row)
        try:
            lat = float(row["point_coordinates_wgs84_lat"])
            lon = float(row["point_coordinates_wgs84_lon"])
        except ValueError:
            raise ForecastError("invalid_point_coordinates") from None
        if (
            len(points) >= 10_000
            or not math.isfinite(lat)
            or not -90 <= lat <= 90
            or not math.isfinite(lon)
            or not -180 <= lon <= 180
            or not 1 <= len(row["point_name"]) <= 256
        ):
            raise ForecastError("invalid_forecast_point")
        point = Point(*identity, row["point_name"], lat, lon)
        if identity in points and points[identity] != point:
            raise ForecastError("conflicting_forecast_point")
        points[identity] = point
    return list(points.values())


def nearest_point(
    points: list[Point], latitude: float, longitude: float
) -> LocatedPoint | None:
    """A nearby postal-code forecast; the 20 km limit is a product cutoff."""
    if not (45.82 <= latitude <= 47.81 and 5.96 <= longitude <= 10.49):
        return None
    candidates = []
    for point in points:
        if point.point_type_id != 2:
            continue
        lat1, lat2 = math.radians(latitude), math.radians(point.latitude)
        delta_lat = lat2 - lat1
        delta_lon = math.radians(point.longitude - longitude)
        haversine = (
            math.sin(delta_lat / 2) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
        )
        distance = 6371 * 2 * math.asin(math.sqrt(min(1, haversine)))
        if distance <= MAX_POINT_DISTANCE_KM:
            candidates.append(LocatedPoint(point, distance))
    return min(
        candidates,
        key=lambda item: (item.distance_km, item.point.point_id),
        default=None,
    )


def parse_radiation(
    data: bytes, released_at: datetime
) -> dict[tuple[int, int], list[Radiation]]:
    """Validate source rows and retain postal intervals through release + 28 h."""
    grouped: dict[tuple[int, int], dict[datetime, Radiation]] = {}
    timestamps: dict[str, tuple[datetime, datetime]] = {}
    identities: set[tuple[int, int]] = set()
    last_end = released_at + timedelta(hours=28)
    for row in _rows(data, {"point_id", "point_type_id", "Date", "gre000h0"}):
        identity = _identity(row)
        identities.add(identity)
        if len(identities) > 10_000:
            raise ForecastError("invalid_forecast_point")
        stamp = row["Date"]
        if stamp not in timestamps:
            try:
                ends_at = datetime.strptime(stamp, "%Y%m%d%H%M").replace(tzinfo=UTC)
            except ValueError:
                raise ForecastError("invalid_forecast_interval") from None
            if len(stamp) != 12 or ends_at.minute != 0 or len(timestamps) >= 1000:
                raise ForecastError("invalid_forecast_interval")
            timestamps[stamp] = (ends_at - timedelta(hours=1), ends_at)
        if row["gre000h0"] == "":
            continue
        if not re.fullmatch(r"\d{1,9}(?:\.\d{1,6})?", row["gre000h0"]):
            raise ForecastError("invalid_forecast_radiation")
        starts_at, ends_at = timestamps[stamp]
        if identity[0] != 2 or not released_at < ends_at <= last_end:
            continue
        value = Decimal(row["gre000h0"])
        reading = Radiation(starts_at, ends_at, value)
        values = grouped.setdefault(identity, {})
        if ends_at in values and values[ends_at] != reading:
            raise ForecastError("conflicting_forecast_interval")
        values[ends_at] = reading
    return {
        key: sorted(values.values(), key=lambda reading: reading.ends_at)
        for key, values in grouped.items()
    }


def release_state(released_at: datetime, now: datetime) -> str:
    if released_at > now:
        return "unavailable"
    return "current" if now - released_at <= RELEASE_MAX_AGE else "stale"
