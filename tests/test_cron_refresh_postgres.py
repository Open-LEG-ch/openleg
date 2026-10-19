# SPDX-License-Identifier: AGPL-3.0-or-later
"""Canton selection through the cron HTTP route and real profile storage."""

import json
import os
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
