# SPDX-License-Identifier: AGPL-3.0-or-later
"""Canton selection through the cron HTTP route and real profile storage."""

import io
import json
import os
import zipfile
from unittest.mock import patch

import pytest
import requests

from tests import test_interest_postgres

interest_database = test_interest_postgres.interest_database

pytestmark = pytest.mark.integration


def _response(content, *, json_body=False):
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps(content).encode() if json_body else content.encode()
    response.encoding = "utf-8"
    return response


@pytest.fixture
def refresh_web(interest_database, monkeypatch):
    environment = {
        "DATABASE_URL": os.environ["DATABASE_URL"],
        "REDIS_URL": "",
    }
    with patch.dict(os.environ, environment, clear=True):
        import app
        import public_data

        csv = (
            "BFS_NR;GEMEINDENAME;KANTON;anteil_dachflaechen_solar;anteil_ev;"
            "anteil_erneuerbar_heizen;stromverbrauch_mwh;erneuerbare_produktion_mwh\n"
            "99971;Synthetic OW;OW;30;10;20;100;40\n"
            "99972;Synthetic ZH;ZH;35;12;25;120;50\n"
        )

        def get(url, **kwargs):
            if url == public_data.ENERGIE_REPORTER_URL:
                return _response(
                    {
                        "result": {
                            "resources": [
                                {
                                    "format": "CSV",
                                    "url": "https://data.example.invalid/reporter.csv",
                                }
                            ]
                        }
                    },
                    json_body=True,
                )
            if url == public_data.SONNENDACH_URL:
                return _response({"result": {"resources": []}}, json_body=True)
            assert url == "https://data.example.invalid/reporter.csv"
            return _response(csv)

        monkeypatch.setattr(requests, "get", get)
        monkeypatch.setattr(
            requests,
            "post",
            lambda *args, **kwargs: _response(
                {"results": {"bindings": []}}, json_body=True
            ),
        )
        yield app.create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "synthetic-refresh",
                "APP_BASE_URL": "http://localhost",
                "PUBLIC_SITE_URL": "http://localhost",
                "RATELIMIT_STORAGE_URI": "memory://",
                "CRON_SECRET": "synthetic-cron-secret",
            },
            load_environment=False,
            check_database=False,
        )


def test_refresh_cron_persists_only_the_requested_canton(
    refresh_web, interest_database
):
    response = refresh_web.test_client().post(
        "/api/cron/refresh-public-data?canton=OW",
        headers={"X-Cron-Secret": "synthetic-cron-secret"},
    )

    assert response.status_code == 200, response.text
    assert response.json["kanton"] == "OW"
    assert response.json["municipalities"] == 1
    assert interest_database.get_municipality_profile(99971)["name"] == "Synthetic OW"
    assert interest_database.get_municipality_profile(99972) is None


def test_refresh_cron_rejects_an_unknown_canton(refresh_web, interest_database):
    response = refresh_web.test_client().post(
        "/api/cron/refresh-public-data?canton=XX",
        headers={"X-Cron-Secret": "synthetic-cron-secret"},
    )

    assert response.status_code == 400
    assert response.json == {"error": "invalid_canton"}
    assert interest_database.get_municipality_profile(99971) is None


def test_refresh_cron_defaults_to_the_tenants_canton(refresh_web, interest_database):
    assert interest_database.upsert_tenant(
        "obwalden", {"kanton_code": "OW", "city_name": "Synthetic OW"}
    )
    response = refresh_web.test_client().post(
        "/api/cron/refresh-public-data",
        base_url="http://obwalden.openleg.ch",
        headers={"X-Cron-Secret": "synthetic-cron-secret"},
    )

    assert response.status_code == 200, response.text
    assert response.json["kanton"] == "OW"
    assert interest_database.get_municipality_profile(99971)["kanton"] == "OW"


def test_refresh_cron_persists_installed_capacity_from_the_reporter_archive(
    refresh_web, interest_database, monkeypatch
):
    import public_data

    content = io.BytesIO()
    with zipfile.ZipFile(content, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "energyreporter_municipality_latest.csv",
            "bfs_nr,municipality,canton,electric_car_share,solar_potential_usage,"
            "solar_power_installed_kwp,renewable_heating_share,"
            "elec_consumption_mwh_per_year,renelec_production_mwh_per_year\n"
            "99971,Synthetic OW,OW,0.125,0.25,1200,0.5,400,150\n",
        )
    previous_get = requests.get

    def get(url, **kwargs):
        if url == public_data.ENERGIE_REPORTER_URL:
            return _response(
                {
                    "result": {
                        "resources": [
                            {
                                "format": "ZIP",
                                "url": "https://data.example.invalid/energyreporter_latest.zip",
                            }
                        ]
                    }
                },
                json_body=True,
            )
        if url == "https://data.example.invalid/energyreporter_latest.zip":
            response = requests.Response()
            response.status_code = 200
            response._content = content.getvalue()
            response._content_consumed = True
            return response
        return previous_get(url, **kwargs)

    monkeypatch.setattr(requests, "get", get)
    response = refresh_web.test_client().post(
        "/api/cron/refresh-public-data?canton=OW",
        headers={"X-Cron-Secret": "synthetic-cron-secret"},
    )

    assert response.status_code == 200, response.text
    profile = interest_database.get_municipality_profile(99971)
    assert profile["solar_installed_kwp"] == 1200
    assert profile["solar_potential_pct"] == 25
    assert profile["ev_share_pct"] == 12.5
    assert profile["electricity_consumption_mwh"] == 400


def test_refresh_preserves_installed_capacity_during_reporter_outage(
    refresh_web, interest_database, monkeypatch
):
    import public_data

    assert interest_database.save_municipality_profile(
        {
            "bfs_number": 99971,
            "name": "Synthetic OW",
            "kanton": "OW",
            "solar_installed_kwp": 1200,
        }
    )

    def get(url, **kwargs):
        if url in (
            public_data.ENERGIE_REPORTER_URL,
            public_data.ENERGIE_REPORTER_ARCHIVE_URL,
        ):
            response = _response("unavailable reporter")
            response.status_code = 503
            return response
        if url == public_data.SONNENDACH_URL:
            return _response(
                {
                    "result": {
                        "resources": [
                            {
                                "format": "CSV",
                                "url": "https://data.example.invalid/solar.csv",
                            }
                        ]
                    }
                },
                json_body=True,
            )
        assert url == "https://data.example.invalid/solar.csv"
        return _response(
            "MunicipalityNumber,Scenario1_RoofsOnly_PotentialSolarElectricity_GWh\n"
            "99971,1.25\n"
        )

    monkeypatch.setattr(requests, "get", get)

    result = public_data.refresh_municipality(99971)

    assert result["sources"]["energie_reporter"] == "fetch_failed"
    assert result["sources"]["sonnendach"] == "ok"
    assert (
        interest_database.get_municipality_profile(99971)["solar_installed_kwp"] == 1200
    )
