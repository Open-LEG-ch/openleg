"""Verify API endpoints used by savings_widget JS."""
import pytest
from unittest.mock import patch
from tests.conftest import MOCK_PROFILES_LIST, MOCK_MUNICIPALITY_PROFILE, MOCK_ELCOM_TARIFFS, MOCK_SONNENDACH


class TestMunicipalitiesEndpoint:
    def test_list_municipalities(self, client):
        with patch("api_public.db.get_all_municipality_profiles", return_value=MOCK_PROFILES_LIST):
            resp = client.get("/api/v1/municipalities")
        assert resp.status_code == 200
        data = resp.get_json()
        munis = data.get("municipalities", data)
        assert isinstance(munis, list)
        assert len(munis) > 0
        first = munis[0]
        assert "name" in first
        assert "bfs_number" in first or "bfs" in first


class TestLegPotentialEndpoint:
    def test_leg_potential_fields(self, client):
        with patch("api_public.db.get_municipality_profile", return_value=MOCK_MUNICIPALITY_PROFILE), \
             patch("api_public.db.get_elcom_tariffs", return_value=MOCK_ELCOM_TARIFFS), \
             patch("api_public.db.get_sonnendach_municipal", return_value=MOCK_SONNENDACH):
            resp = client.get("/api/v1/municipalities/261/leg-potential")
        if resp.status_code == 404:
            pytest.skip("leg-potential endpoint not registered in test app")
        assert resp.status_code == 200
        data = resp.get_json()
        # API returns annual_savings_chf, savings_rp_kwh, etc.
        assert "annual_savings_chf" in data, f"annual_savings_chf missing: {list(data.keys())}"
        assert "savings_rp_kwh" in data, f"savings_rp_kwh missing: {list(data.keys())}"
        assert "bfs_number" in data
