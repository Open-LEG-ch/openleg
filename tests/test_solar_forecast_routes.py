# SPDX-License-Identifier: AGPL-3.0-or-later
"""Owner forecast context and authenticated scheduled refresh."""

from unittest.mock import MagicMock

import pytest

import solar_forecast_cache as weather
from tests import test_telemetry as api

app_module = api.app_module
interest_database = api.interest_database
telemetry_client = api.telemetry_client
pytestmark = pytest.mark.integration


def test_owner_page_separates_cached_forecast_from_measurements(
    telemetry_client, monkeypatch
):
    client = telemetry_client
    installation, token = api.enroll(client)
    assert (
        api.ingest(client, installation, token, [api.sample(value="42")]).status_code
        == 201
    )
    cached = MagicMock(
        return_value={
            "state": "current",
            "released_at": "2026-10-25T01:00:00+00:00",
            "released_label": "25.10.2026 02:00 CET (+0100)",
            "source_url": "https://data.geo.admin.ch/public-weather.csv",
            "point_name": "<script>public point</script>",
            "distance_km": 1.2,
            "intervals": [
                {
                    "starts_at": "2026-10-25T01:00:00+00:00",
                    "ends_at": "2026-10-25T02:00:00+00:00",
                    "local_label": "25.10.2026 02:00 CET (+0100)",
                    "local_end_label": "25.10.2026 03:00 CET (+0100)",
                    "radiation_w_m2": "0",
                },
                {
                    "starts_at": "2026-10-25T02:00:00+00:00",
                    "ends_at": "2026-10-25T03:00:00+00:00",
                    "local_label": "25.10.2026 03:00 CET (+0100)",
                    "local_end_label": "25.10.2026 04:00 CET (+0100)",
                    "radiation_w_m2": None,
                },
            ],
        }
    )
    monkeypatch.setattr(weather, "view", cached)
    download = MagicMock(
        side_effect=AssertionError("owner requests must use cache only")
    )
    monkeypatch.setattr(weather, "_download", download)
    response = client.get("/dashboard/telemetry?lang=fr-CH")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert "Prévisions météorologiques" in response.text
    assert "42 W" in response.text and "0 W/m²" in response.text
    assert response.text.index("42 W") < response.text.index(
        "Prévisions météorologiques"
    )
    assert "MeteoSwiss" in response.text and "1,2 km" in response.text
    assert "25.10.2026 02:00 CET (+0100)" in response.text
    assert "&lt;script&gt;public point&lt;/script&gt;" in response.text
    assert "<script>public point</script>" not in response.text
    assert "CHF" not in response.text
    assert cached.call_count == 1
    download.assert_not_called()


@pytest.mark.parametrize("state", ["unavailable", "stale"])
def test_unavailable_weather_preserves_readings(telemetry_client, monkeypatch, state):
    client = telemetry_client
    installation, token = api.enroll(client)
    assert (
        api.ingest(client, installation, token, [api.sample(value="42")]).status_code
        == 201
    )
    monkeypatch.setattr(weather, "view", lambda *a, **k: {"state": state})
    page = client.get("/dashboard/telemetry")
    assert page.status_code == 200
    assert "42 W" in page.text
    assert "Wetterprognose derzeit nicht verfügbar" in page.text
    assert "W/m²" not in page.text


def test_forecast_refresh_requires_cron_secret(telemetry_client, monkeypatch):
    refresh = MagicMock(return_value={"state": "updated"})
    monkeypatch.setattr(weather, "refresh", refresh)
    path = "/api/cron/refresh-solar-forecast"
    assert telemetry_client.post(path).status_code == 403
    refresh.assert_not_called()
    response = telemetry_client.post(
        path, headers={"X-Cron-Secret": "test-cron-secret"}
    )
    assert response.status_code == 200
    assert response.json == {"state": "updated"}
