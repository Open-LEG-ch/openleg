# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deployment-wide public weather cache. Owner requests never fetch upstream."""

import hashlib
import json
import logging
import re
import ssl
import time
import uuid
from datetime import UTC, datetime, timedelta
from http.client import HTTPSConnection
from socket import SHUT_RDWR
from threading import Timer
from typing import Any
from urllib.parse import urlsplit

import cache
import solar_forecast as forecast

logger = logging.getLogger(__name__)

COLLECTION_URL = "https://data.geo.admin.ch/api/stac/v1/collections/ch.meteoschweiz.ogd-local-forecasting"
ITEMS_URL = COLLECTION_URL + "/items?limit=3&sortby=-id"
POINTS_NAME = "ogd-local-forecasting_meta_point.csv"
KEY_PREFIX = "openleg:solar-forecast:"
PUBLISHED = KEY_PREFIX + "published"
LEASE = "openleg:solar-forecast:refresh-lease"
ATTEMPT = "openleg:solar-forecast:attempt"
CACHE_SECONDS = 4 * 3600
PUBLISH = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
local previous = redis.call('GET', KEYS[2])
local incoming = cjson.decode(ARGV[2])
if previous then
  local ok, decoded = pcall(cjson.decode, previous)
  if ok and decoded.released_at and decoded.released_at > incoming.released_at then return -1 end
end
redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3])
return 1
"""
RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""


def point_key(version: str, kind: int, identifier: int) -> str:
    return f"{KEY_PREFIX}{version}:{kind}:{identifier}"


def _download(url: str, limit: int) -> bytes:
    """Fixed official host, verified TLS, no proxies or redirects, bounded body."""
    allowed = url in {
        COLLECTION_URL,
        ITEMS_URL,
        forecast.SOURCE_PREFIX + POINTS_NAME,
    } or re.fullmatch(
        re.escape(forecast.SOURCE_PREFIX)
        + r"\d{8}-ch/vnut12\.lssw\.\d{12}\.gre000h0\.csv",
        url,
    )
    if not allowed:
        raise forecast.ForecastError("invalid_forecast_url")
    parsed = urlsplit(url)
    deadline = time.monotonic() + 20
    connection = HTTPSConnection(
        "data.geo.admin.ch", timeout=5, context=ssl.create_default_context()
    )
    watchdog = None
    try:
        connection.connect()
        sock = connection.sock
        assert sock is not None

        def expire():
            try:
                sock.shutdown(SHUT_RDWR)
            except OSError:
                pass

        watchdog = Timer(max(0, deadline - time.monotonic()), expire)
        watchdog.daemon = True
        watchdog.start()
        target = parsed.path + ("?" + parsed.query if parsed.query else "")
        connection.request("GET", target, headers={"Accept-Encoding": "identity"})
        with connection.getresponse() as response:
            if (
                response.status != 200
                or response.headers.get("Content-Encoding", "identity") != "identity"
            ):
                raise forecast.ForecastError("invalid_forecast_response")
            if int(response.headers.get("Content-Length", "0")) > limit:
                raise forecast.ForecastError("forecast_download_too_large")
            body = bytearray()
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError()
                chunk = response.read1(min(8192, limit + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > limit:
                    raise forecast.ForecastError("forecast_download_too_large")
        if time.monotonic() >= deadline:
            raise TimeoutError()
        return bytes(body)
    finally:
        if watchdog is not None:
            watchdog.cancel()
        connection.close()


def _checked(download, url: str, checksum: str, limit: int) -> bytes:
    if not isinstance(checksum, str) or not re.fullmatch(r"1220[0-9a-f]{64}", checksum):
        raise forecast.ForecastError("invalid_forecast_checksum")
    body = download(url, limit)
    if len(body) > limit or hashlib.sha256(body).hexdigest() != checksum[4:]:
        raise forecast.ForecastError("forecast_checksum_mismatch")
    return body


def refresh(
    *, now: datetime | None = None, redis_client=None, download=None
) -> dict[str, str]:
    """One scheduled attempt per 15 minutes; publish a complete generation last."""
    now = now or datetime.now(UTC)
    token = uuid.uuid4().hex
    client = None
    held = False
    stage = "cache"
    try:
        client = redis_client if redis_client is not None else cache._get_redis()
        if not client.set(LEASE, token, nx=True, ex=120):
            return {"state": "busy"}
        held = True
        if not client.set(ATTEMPT, "1", nx=True, ex=840):
            return {"state": "cooldown"}
        get = download or _download
        stage = "metadata"
        collection = json.loads(get(COLLECTION_URL, 2 * 1024 * 1024))
        items = json.loads(get(ITEMS_URL, 2 * 1024 * 1024))
        release = forecast.select_release(items, now)
        if (
            release is None
            or forecast.release_state(release.released_at, now) != "current"
        ):
            return {"state": "unavailable"}
        metadata = collection["assets"][POINTS_NAME]
        points_url = forecast.SOURCE_PREFIX + POINTS_NAME
        if metadata["href"] != points_url:
            raise forecast.ForecastError("invalid_point_metadata_url")
        if not isinstance(metadata["file:checksum"], str) or not re.fullmatch(
            r"1220[0-9a-f]{64}", metadata["file:checksum"]
        ):
            raise forecast.ForecastError("invalid_forecast_checksum")
        version = (
            release.released_at.strftime("%Y%m%d%H%M")
            + "-"
            + release.sha256
            + "-"
            + metadata["file:checksum"][4:]
        )
        existing: dict[str, Any] = {}
        try:
            cached = client.get(PUBLISHED)
            if cached is not None and len(cached) <= 2 * 1024 * 1024:
                existing = json.loads(cached)
        except (ValueError, TypeError):
            logger.debug("Replacing malformed forecast cache header")
        if (
            isinstance(existing, dict)
            and existing.get("version") == version
            and existing.get("released_at") == release.released_at.isoformat()
            and isinstance(existing.get("points"), list)
            and 0 < len(existing["points"]) <= 10_000
        ):
            try:
                keys = [
                    point_key(version, point[0], point[1])
                    for point in existing["points"]
                ]
            except (TypeError, IndexError):
                keys = []
            if keys and sum(
                client.exists(*keys[index : index + 500])
                for index in range(0, len(keys), 500)
            ) == len(keys):
                return {"state": "unchanged"}
        stage = "points"
        points = forecast.parse_points(
            _checked(get, points_url, metadata["file:checksum"], 2 * 1024 * 1024)
        )
        stage = "radiation"
        radiation = forecast.parse_radiation(
            _checked(
                get, release.source_url, "1220" + release.sha256, forecast.MAX_CSV_BYTES
            ),
            release.released_at,
        )
        stage = "publication"
        pipeline = client.pipeline(transaction=False)
        selected = [point for point in points if point.point_type_id == 2]
        for index, point in enumerate(selected):
            values = radiation.get((point.point_type_id, point.point_id), [])
            pipeline.set(
                point_key(version, point.point_type_id, point.point_id),
                json.dumps(
                    [
                        [reading.ends_at.isoformat(), str(reading.radiation_w_m2)]
                        for reading in values
                    ]
                ),
                ex=CACHE_SECONDS,
            )
            if (index + 1) % 500 == 0:
                pipeline.execute()
        pipeline.execute()
        header = json.dumps(
            {
                "version": version,
                "released_at": release.released_at.isoformat(),
                "source_url": release.source_url,
                "points": [
                    [
                        point.point_type_id,
                        point.point_id,
                        point.name,
                        point.latitude,
                        point.longitude,
                    ]
                    for point in selected
                ],
            }
        )
        published = client.eval(
            PUBLISH, 2, LEASE, PUBLISHED, token, header, CACHE_SECONDS
        )
        return {
            "state": "updated"
            if published == 1
            else "unchanged"
            if published == -1
            else "unavailable"
        }
    except Exception as exc:
        logger.warning(
            "Solar forecast refresh failed at %s (%s)", stage, type(exc).__name__
        )
        return {"state": "unavailable"}
    finally:
        if held and client is not None:
            try:
                client.eval(RELEASE, 1, LEASE, token)
            except Exception:
                logger.debug("Forecast refresh lease cleanup failed")


def view(
    latitude, longitude, *, now: datetime | None = None, redis_client=None
) -> dict[str, Any]:
    """Read cached public data only; coordinates remain within this deployment."""
    now = now or datetime.now(UTC)
    try:
        client = redis_client if redis_client is not None else cache._get_redis()
        raw = client.get(PUBLISHED)
        if raw is None or len(raw) > 2 * 1024 * 1024:
            return {"state": "unavailable"}
        header = json.loads(raw)
        released_at = datetime.fromisoformat(header["released_at"])
        stamp = released_at.strftime("%Y%m%d%H%M")
        expected_url = (
            f"{forecast.SOURCE_PREFIX}{stamp[:8]}-ch/vnut12.lssw.{stamp}.gre000h0.csv"
        )
        if (
            released_at.utcoffset() != timedelta(0)
            or header["source_url"] != expected_url
            or not re.fullmatch(
                stamp + r"-[0-9a-f]{64}-[0-9a-f]{64}", header["version"]
            )
            or not isinstance(header["points"], list)
            or len(header["points"]) > 10_000
        ):
            return {"state": "unavailable"}
        state = forecast.release_state(released_at, now)
        if state != "current":
            return {"state": state}
        points = [forecast.Point(*row) for row in header["points"]]
        located = forecast.nearest_point(points, float(latitude), float(longitude))
        if located is None:
            return {"state": "unavailable"}
        point = located.point
        raw = client.get(
            point_key(header["version"], point.point_type_id, point.point_id)
        )
        if raw is None or len(raw) > 64 * 1024:
            return {"state": "unavailable"}
        rows = json.loads(raw)
        if not isinstance(rows, list) or len(rows) > 1000:
            return {"state": "unavailable"}
        values: dict[datetime, str] = {}
        for stamp, value in rows:
            end = datetime.fromisoformat(stamp)
            if (
                end.utcoffset() != timedelta(0)
                or end.minute
                or end.second
                or end.microsecond
                or not isinstance(value, str)
                or not re.fullmatch(r"[0-9]{1,9}(?:\.[0-9]{1,6})?", value)
                or (end in values and values[end] != value)
            ):
                return {"state": "unavailable"}
            values[end] = value
        first_end = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        if not any(
            first_end <= end < first_end + timedelta(hours=24) for end in values
        ):
            return {"state": "unavailable"}
        intervals = []
        for offset in range(24):
            end = first_end + timedelta(hours=offset)
            start = end - timedelta(hours=1)
            intervals.append(
                {
                    "starts_at": start.isoformat(),
                    "ends_at": end.isoformat(),
                    "radiation_w_m2": values.get(end),
                    "local_label": start.astimezone(forecast.SWISS_TIME).strftime(
                        "%d.%m.%Y %H:%M %Z (%z)"
                    ),
                    "local_end_label": end.astimezone(forecast.SWISS_TIME).strftime(
                        "%d.%m.%Y %H:%M %Z (%z)"
                    ),
                }
            )
        return {
            "state": "current",
            "released_at": header["released_at"],
            "released_label": released_at.astimezone(forecast.SWISS_TIME).strftime(
                "%d.%m.%Y %H:%M %Z (%z)"
            ),
            "source_url": header["source_url"],
            "point_name": point.name,
            "distance_km": located.distance_km,
            "intervals": intervals,
        }
    except Exception:
        return {"state": "unavailable"}
