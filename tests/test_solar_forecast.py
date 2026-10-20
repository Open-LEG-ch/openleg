# SPDX-License-Identifier: AGPL-3.0-or-later
"""Documented public weather data, never an installation production estimate."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

import solar_forecast as forecast

NOW = datetime(2026, 10, 25, 1, 30, tzinfo=UTC)
RELEASED_AT = datetime(2026, 10, 25, tzinfo=UTC)
PREFIX = "https://data.geo.admin.ch/ch.meteoschweiz.ogd-local-forecasting/"


def asset(release="202610250100", **changes):
    name = f"vnut12.lssw.{release}.gre000h0.csv"
    return name, {
        "href": f"{PREFIX}{release[:8]}-ch/{name}",
        "file:checksum": "1220" + "a" * 64,
        **changes,
    }


def items(*assets):
    # STAC item datetime is deliberately unrelated to a parameter release.
    return {
        "features": [
            {"properties": {"datetime": "2000-01-01T00:00:00Z"}, "assets": dict(assets)}
        ]
    }


def test_release_uses_parameter_asset_and_rejects_redirect_targets_and_future_data():
    selected = forecast.select_release(items(asset(), asset("202610250000")), NOW)
    assert selected.released_at == datetime(2026, 10, 25, 1, tzinfo=UTC)
    assert selected.sha256 == "a" * 64
    assert forecast.select_release(items(asset("202610250200")), NOW) is None
    for bad in (
        "https://example.com/data.csv",
        PREFIX + "../private.csv",
        PREFIX + "20261025-ch/other.csv",
    ):
        assert forecast.select_release(items(asset(href=bad)), NOW) is None
    assert (
        forecast.select_release(items(asset(**{"file:checksum": "invalid"})), NOW)
        is None
    )


def test_point_identity_includes_type_and_distance():
    data = (
        "point_id;point_type_id;point_name;point_coordinates_wgs84_lat;point_coordinates_wgs84_lon\n"
        "1;1;Station;47.0;8.0\n"
        "1;2;Münchenbuchsee;47.001;8.0\n"
        "2;2;Other;47.1;8.0\n"
    ).encode("latin-1")
    points = forecast.parse_points(data)
    assert len(points) == 3
    selected = forecast.nearest_point(points, 47.0, 8.0)
    assert selected.point.point_type_id == 2
    assert selected.point.point_id == 1
    assert selected.point.name == "Münchenbuchsee"
    assert 0.1 < selected.distance_km < 0.2
    assert forecast.nearest_point(points, 46.0, 7.0) is None
    assert forecast.nearest_point(points, 48.5, 8.0) is None
    assert forecast.nearest_point(points, float("nan"), 8.0) is None


def test_identical_metadata_repetition_is_coalesced_but_conflicts_are_rejected():
    head = "point_id;point_type_id;point_name;point_coordinates_wgs84_lat;point_coordinates_wgs84_lon\n"
    row = "123;1;Seengen;47.327328;8.18845\n"
    assert len(forecast.parse_points((head + row + row).encode())) == 1
    with pytest.raises(forecast.ForecastError):
        forecast.parse_points((head + row + row.replace("8.18845", "8.2")).encode())


def test_interval_end_is_utc_and_both_dst_hours_remain_distinct():
    data = (
        "point_id;point_type_id;Date;gre000h0\n"
        "1;1;202610250100;999\n"
        "1;2;202610250100;0\n"
        "1;2;202610250200;12.3\n"
        "1;2;202610250300;\n"
    ).encode("ascii")
    rows = forecast.parse_radiation(data, RELEASED_AT)
    readings = rows[(2, 1)]
    assert len(readings) == 2
    assert readings[0].starts_at == datetime(2026, 10, 25, 0, tzinfo=UTC)
    assert readings[0].ends_at == datetime(2026, 10, 25, 1, tzinfo=UTC)
    assert readings[0].radiation_w_m2 == Decimal(0)
    assert readings[1].radiation_w_m2 == Decimal("12.3")
    local = [r.ends_at.astimezone(forecast.SWISS_TIME) for r in readings]
    assert [x.hour for x in local] == [2, 3]
    assert local[0].utcoffset() == timedelta(hours=1)
    assert readings[0].starts_at.astimezone(forecast.SWISS_TIME).hour == 2
    assert readings[0].starts_at.astimezone(
        forecast.SWISS_TIME
    ).utcoffset() == timedelta(hours=2)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "1e1000", "1,2"])
def test_invalid_radiation_is_rejected_without_becoming_zero(value):
    data = f"point_id;point_type_id;Date;gre000h0\n1;2;202610250100;{value}\n".encode()
    with pytest.raises(forecast.ForecastError):
        forecast.parse_radiation(data, RELEASED_AT)


def test_conflicting_duplicates_and_wrong_intervals_are_rejected():
    head = "point_id;point_type_id;Date;gre000h0\n"
    for body in ("1;2;202610250130;12\n", "1;2;202610250100;12\n1;2;202610250100;13\n"):
        with pytest.raises(forecast.ForecastError):
            forecast.parse_radiation((head + body).encode(), RELEASED_AT)


def test_retained_postal_intervals_cover_the_view_through_the_release_freshness_window():
    head = "point_id;point_type_id;Date;gre000h0\n"
    body = "".join(
        f"1;{kind};{(RELEASED_AT + timedelta(hours=hour)):%Y%m%d%H%M};{hour + 1}\n"
        for kind in (1, 2, 3)
        for hour in range(-1, 30)
    )
    rows = forecast.parse_radiation((head + body).encode(), RELEASED_AT)
    assert set(rows) == {(2, 1)}
    assert [reading.ends_at for reading in rows[(2, 1)]] == [
        RELEASED_AT + timedelta(hours=hour) for hour in range(1, 29)
    ]


def test_discarded_intervals_still_reject_malformed_source_values():
    data = b"point_id;point_type_id;Date;gre000h0\n1;1;202611010100;NaN\n"
    with pytest.raises(forecast.ForecastError):
        forecast.parse_radiation(data, RELEASED_AT)


def test_release_age_distinguishes_current_stale_and_future():
    release = forecast.select_release(items(asset()), NOW)
    assert release is not None
    assert forecast.release_state(release.released_at, NOW) == "current"
    assert (
        forecast.release_state(release.released_at, NOW + timedelta(hours=3)) == "stale"
    )
    assert (
        forecast.release_state(release.released_at, NOW - timedelta(hours=1))
        == "unavailable"
    )
