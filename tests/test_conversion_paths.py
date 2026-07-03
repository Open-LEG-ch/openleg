# SPDX-License-Identifier: AGPL-3.0-or-later
"""Conversion-path contract tests for the public intake flows (issue #109).

An adversarially verified audit found the primary funnel broken end to end:
the landing form posted to a nonexistent route with a wrong payload contract,
the calculator crashed on every run, rate limits surfaced as English HTML,
and legitimate Swiss addresses were rejected. These tests pin the fixes.
"""

import importlib
import os
from unittest.mock import MagicMock, patch

import pytest
from werkzeug.exceptions import TooManyRequests

import security_utils

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read_template(name):
    with open(
        os.path.join(PROJECT_ROOT, "templates", name), encoding="utf-8"
    ) as handle:
        return handle.read()


@pytest.fixture
def full_app_module():
    with patch.dict(
        os.environ,
        {
            "DATABASE_URL": "postgresql://x:x@localhost/x",
            "REDIS_URL": "memory://",
            "CRON_SECRET": "test-cron-secret",
            "APP_BASE_URL": "http://localhost:5003",
        },
    ):
        with (
            patch("database.is_db_available", return_value=True),
            patch("database._connection_pool", MagicMock()),
        ):
            import app as app_module

            app_module = importlib.reload(app_module)
            yield app_module


# === Landing page registration funnel (index.html) ===


class TestIndexRegistrationContract:
    @pytest.fixture(autouse=True)
    def load(self):
        self.html = _read_template("index.html")

    def test_posts_to_existing_registration_endpoint(self):
        assert "register_interest" not in self.html, (
            "index.html still posts to /api/register_interest, "
            "a route that does not exist"
        )
        assert "/api/register_anonymous" in self.html

    def test_sends_profile_under_server_field_name(self):
        assert "profile_summary: selectedProfile" not in self.html, (
            "server reads request.json['profile'], not profile_summary"
        )
        assert "profile: selectedProfile" in self.html

    def test_success_not_gated_on_missing_success_key(self):
        assert "data.success" not in self.html, (
            "the registration endpoints never return a success key; "
            "gate on res.ok instead"
        )
        assert "res.ok" in self.html

    def test_required_consents_validated_client_side(self):
        assert "!cn || !cu" in self.html.replace("(", "").replace(")", ""), (
            "the two mandatory consents must be validated before submit"
        )

    def test_referral_code_forwarded_from_ref_param(self):
        assert "referral_code" in self.html, (
            "?ref= invitation links are handed out but never redeemed; "
            "the registration payload must include referral_code"
        )

    def test_autocomplete_failures_are_handled(self):
        suggest_section = self.html.split("suggest_addresses")[1][:600]
        assert "catch" in suggest_section, (
            "the address-autocomplete fetch has no error handling; failures "
            "are unhandled promise rejections that dead-end the funnel"
        )


# === LEG-Kalkulator (leg_kalkulator.html) ===


class TestKalkulatorContract:
    @pytest.fixture(autouse=True)
    def load(self):
        self.html = _read_template("leg_kalkulator.html")

    def test_unwraps_tariffs_object(self):
        assert "data.tariffs" in self.html, (
            "/api/v1/municipalities/<bfs>/tariffs returns an object "
            "{bfs_number, tariffs, count}; the JS must unwrap data.tariffs"
        )

    def test_reads_real_tariff_field_names(self):
        assert "energy_rp_kwh" in self.html
        assert "grid_rp_kwh" in self.html
        assert "tariff.energy_price" not in self.html
        assert "tariff.grid_price" not in self.html

    def test_no_raw_alert_error_handling(self):
        assert "alert(" not in self.html, (
            "raw browser alerts are dead ends; render inline German errors"
        )


# === Server: municipality search parameter ===


class TestMunicipalitySearch:
    def test_search_param_filters_by_name(self, client):
        profiles = [
            {"bfs_number": 4021, "name": "Baden", "kanton": "AG"},
            {"bfs_number": 261, "name": "Zürich", "kanton": "ZH"},
        ]
        with patch("api_public.db") as mock_db:
            mock_db.get_all_municipality_profiles.return_value = profiles
            resp = client.get("/api/v1/municipalities?search=bad")
        assert resp.status_code == 200
        data = resp.get_json()
        names = [m["name"] for m in data["municipalities"]]
        assert names == ["Baden"], (
            f"?search= must filter by name substring, got {names}"
        )


# === Server: JSON 429 handler ===


class TestRateLimitResponse:
    def test_429_returns_german_json(self, full_app_module):
        app = full_app_module.app
        handler = app.error_handler_spec[None].get(429)
        assert handler, (
            "no 429 errorhandler registered; rate-limited visitors get an English HTML page"
        )
        handler_fn = next(iter(handler.values()))
        with app.test_request_context("/api/check_potential"):
            response = app.make_response(handler_fn(TooManyRequests()))
        assert response.status_code == 429
        assert response.is_json
        assert "Anfragen" in response.get_json()["error"]


# === Server: Swiss address validation ===


class TestAddressValidation:
    @pytest.mark.parametrize(
        "address",
        [
            "Rue de l'Hôpital 2, 2000 Neuchâtel",
            "Rue du Rhône 1, 1204 Genève",
            "Chemin du Château 5, 1095 Lutry",
            "Bahnhofstrasse 1, 8001 Zürich",
        ],
    )
    def test_accepts_real_swiss_addresses(self, address):
        is_valid, _sanitized, error = security_utils.validate_address(address)
        assert is_valid, f"legitimate Swiss address rejected: {address!r} ({error})"
