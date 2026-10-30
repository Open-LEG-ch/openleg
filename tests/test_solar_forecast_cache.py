# SPDX-License-Identifier: AGPL-3.0-or-later
"""Public refresh/view contracts through real isolated Redis keys."""

import hashlib
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import pytest
import redis

import solar_forecast_cache as weather
from tests.test_solar_forecast import PREFIX, asset, items

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 25, 1, 30, tzinfo=UTC)
POINTS = (
    b"point_id;point_type_id;point_name;point_coordinates_wgs84_lat;point_coordinates_wgs84_lon\n"
    b"1;2;Public forecast point;47.001;8.0\n"
)
RADIATION = (
    b"point_id;point_type_id;Date;gre000h0\n1;2;202610250200;100\n1;2;202610250300;0\n"
)


@pytest.fixture
def forecast_redis(monkeypatch):
    url = os.environ.get("OPENLEG_TEST_FORECAST_REDIS_URL")
    if not url:
        pytest.skip(
            "OPENLEG_TEST_FORECAST_REDIS_URL is required for native Redis evidence"
        )
    assert urlsplit(url).hostname in {"127.0.0.1", "localhost"}
    client = redis.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
    client.ping()
    prefix = "openleg:test-forecast:" + uuid.uuid4().hex + ":"
    monkeypatch.setattr(weather, "KEY_PREFIX", prefix)
    for name, suffix in (
        ("PUBLISHED", "published"),
        ("LEASE", "lease"),
        ("ATTEMPT", "attempt"),
    ):
        monkeypatch.setattr(weather, name, prefix + suffix)
    try:
        yield client
    finally:
        # Delete only this fixture's namespace, never flush a database.
        keys = list(client.scan_iter(match=prefix + "*"))
        if keys:
            client.delete(*keys)
        client.close()


def responses(release="202610250100", radiation=RADIATION):
    name, data = asset(
        release, **{"file:checksum": "1220" + hashlib.sha256(radiation).hexdigest()}
    )
    return {
        weather.COLLECTION_URL: json.dumps(
            {
                "assets": {
                    weather.POINTS_NAME: {
                        "href": PREFIX + weather.POINTS_NAME,
                        "file:checksum": "1220" + hashlib.sha256(POINTS).hexdigest(),
                    }
                }
            }
        ).encode(),
        weather.ITEMS_URL: json.dumps(items((name, data))).encode(),
        PREFIX + weather.POINTS_NAME: POINTS,
        data["href"]: radiation,
    }


def publish(client, *, release="202610250100", radiation=RADIATION, now=NOW):
    downloads = responses(release, radiation)
    return weather.refresh(
        now=now, redis_client=client, download=lambda url, limit: downloads[url]
    )


def read(client, now=NOW):
    return weather.view(47.0, 8.0, now=now, redis_client=client)


def test_refresh_and_view_preserve_zero_missing_data_location_and_interval(
    forecast_redis,
):
    assert publish(forecast_redis) == {"state": "updated"}
    result = read(forecast_redis)
    assert result["state"] == "current"
    assert result["released_at"] == "2026-10-25T01:00:00+00:00"
    assert result["point_name"] == "Public forecast point"
    assert 0.1 < result["distance_km"] < 0.2
    assert result["intervals"][0]["starts_at"] == "2026-10-25T01:00:00+00:00"
    assert result["intervals"][0]["radiation_w_m2"] == "100"
    assert result["intervals"][1]["radiation_w_m2"] == "0"
    assert result["intervals"][2]["radiation_w_m2"] is None
    assert len(result["intervals"]) == 24
    assert 0 < forecast_redis.ttl(weather.PUBLISHED) <= 14400
    assert weather.view(46, 7, now=NOW, redis_client=forecast_redis) == {
        "state": "unavailable"
    }
    assert read(forecast_redis, NOW + timedelta(hours=4)) == {"state": "stale"}


@pytest.mark.parametrize("failure", ["download", "checksum", "pipeline"])
def test_failed_refresh_keeps_previous_complete_public_view(
    forecast_redis, failure, caplog
):
    assert publish(forecast_redis)["state"] == "updated"
    forecast_redis.delete(weather.ATTEMPT)
    downloads = responses(radiation=RADIATION.replace(b"100", b"200"))
    if failure == "checksum":
        downloads[asset()[1]["href"]] = RADIATION.replace(b"100", b"300")

    def download(url, limit):
        if failure == "download":
            raise TimeoutError("private upstream context")
        return downloads[url]

    class FailingPipeline:
        def __init__(self):
            self.pipeline = forecast_redis.pipeline(transaction=False)

        def set(self, *args, **kwargs):
            self.pipeline.set(*args, **kwargs)

        def execute(self):
            self.pipeline.execute()
            raise ConnectionError("stop before publication")

    class FailingClient:
        def __getattr__(self, name):
            return getattr(forecast_redis, name)

        def pipeline(self, **kwargs):
            return FailingPipeline()

    client = FailingClient() if failure == "pipeline" else forecast_redis
    assert weather.refresh(now=NOW, redis_client=client, download=download) == {
        "state": "unavailable"
    }
    assert read(forecast_redis)["intervals"][0]["radiation_w_m2"] == "100"
    stage, error = {
        "download": ("metadata", "TimeoutError"),
        "checksum": ("radiation", "ForecastError"),
        "pipeline": ("publication", "ConnectionError"),
    }[failure]
    assert f"Solar forecast refresh failed at {stage} ({error})" in caplog.text
    assert "private upstream context" not in caplog.text
    assert "stop before publication" not in caplog.text


def test_lost_lease_cannot_publish_or_delete_a_replacement_workers_lock(forecast_redis):
    assert publish(forecast_redis)["state"] == "updated"
    forecast_redis.delete(weather.ATTEMPT)
    downloads = responses(radiation=RADIATION.replace(b"100", b"200"))

    def download(url, limit):
        if url.endswith("gre000h0.csv"):
            forecast_redis.set(weather.LEASE, "replacement", ex=120)
        return downloads[url]

    assert weather.refresh(now=NOW, redis_client=forecast_redis, download=download) == {
        "state": "unavailable"
    }
    assert read(forecast_redis)["intervals"][0]["radiation_w_m2"] == "100"
    assert forecast_redis.get(weather.LEASE) == b"replacement"


def test_an_older_release_cannot_replace_a_newer_forecast(forecast_redis):
    now = NOW + timedelta(hours=1)
    assert (
        publish(forecast_redis, release="202610250200", now=now)["state"] == "updated"
    )
    forecast_redis.delete(weather.ATTEMPT)
    assert publish(forecast_redis, now=now) == {"state": "unchanged"}
    assert read(forecast_redis, now)["released_at"] == "2026-10-25T02:00:00+00:00"


def test_busy_and_cooldown_states_do_not_download(forecast_redis):
    def forbidden(*args):
        raise AssertionError("no download during cooldown or another refresh")

    forecast_redis.set(weather.LEASE, "another-worker", ex=120)
    assert weather.refresh(
        now=NOW, redis_client=forecast_redis, download=forbidden
    ) == {"state": "busy"}
    forecast_redis.delete(weather.LEASE)
    assert publish(forecast_redis)["state"] == "updated"
    # Leave a minute for scheduler jitter before the next 15-minute tick.
    assert 0 < forecast_redis.ttl(weather.ATTEMPT) <= 840
    assert weather.refresh(
        now=NOW, redis_client=forecast_redis, download=forbidden
    ) == {"state": "cooldown"}


def test_cached_serving_window_still_covers_24_hours_at_the_freshness_boundary(
    forecast_redis,
):
    release = NOW.replace(minute=0)
    body = b"point_id;point_type_id;Date;gre000h0\n" + b"".join(
        f"1;2;{release + timedelta(hours=hour):%Y%m%d%H%M};{hour}\n".encode()
        for hour in range(1, 30)
    )
    assert publish(forecast_redis, radiation=body)["state"] == "updated"
    result = read(forecast_redis, release + timedelta(hours=3))
    assert result["state"] == "current"
    assert [row["radiation_w_m2"] for row in result["intervals"]] == [
        str(hour) for hour in range(4, 28)
    ]
    header = json.loads(forecast_redis.get(weather.PUBLISHED))
    cached = json.loads(forecast_redis.get(weather.point_key(header["version"], 2, 1)))
    assert len(cached) == 28


def test_empty_or_missing_selected_point_is_unavailable(forecast_redis):
    assert read(forecast_redis) == {"state": "unavailable"}
    assert publish(forecast_redis)["state"] == "updated"
    header = json.loads(forecast_redis.get(weather.PUBLISHED))
    forecast_redis.delete(weather.point_key(header["version"], 2, 1))
    assert read(forecast_redis) == {"state": "unavailable"}


def test_missing_next_day_coverage_is_unavailable(forecast_redis):
    later = b"point_id;point_type_id;Date;gre000h0\n1;2;202611010000;100\n"
    assert publish(forecast_redis, radiation=later)["state"] == "updated"
    assert read(forecast_redis) == {"state": "unavailable"}


@pytest.mark.parametrize("corruption", ["source_link", "non_numeric_value"])
def test_malformed_cached_content_is_unavailable(forecast_redis, corruption):
    assert publish(forecast_redis)["state"] == "updated"
    header = json.loads(forecast_redis.get(weather.PUBLISHED))
    if corruption == "source_link":
        header["source_url"] = "javascript:alert(1)"
        forecast_redis.set(weather.PUBLISHED, json.dumps(header))
    else:
        forecast_redis.set(
            weather.point_key(header["version"], 2, 1),
            json.dumps([["2026-10-25T02:00:00+00:00", "NaN"]]),
        )
    assert read(forecast_redis) == {"state": "unavailable"}


def test_unchanged_complete_generation_does_not_download_csv_again(forecast_redis):
    assert publish(forecast_redis)["state"] == "updated"
    forecast_redis.delete(weather.ATTEMPT)
    downloads = responses()
    called = []

    def download(url, limit):
        called.append(url)
        return downloads[url]

    assert weather.refresh(now=NOW, redis_client=forecast_redis, download=download) == {
        "state": "unchanged"
    }
    assert called == [weather.COLLECTION_URL, weather.ITEMS_URL]
    assert read(forecast_redis)["intervals"][0]["radiation_w_m2"] == "100"
