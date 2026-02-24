"""Tests for api_public.py: REST API endpoints."""
import json
import pytest
from unittest.mock import patch, MagicMock
from tests.conftest import (
    MOCK_ELCOM_TARIFFS, MOCK_MUNICIPALITY_PROFILE,
    MOCK_SONNENDACH, MOCK_PROFILES_LIST
)


class TestMunicipalityEndpoints:

    @patch('api_public.db')
    def test_list_municipalities(self, mock_db, client):
        mock_db.get_all_municipality_profiles.return_value = MOCK_PROFILES_LIST
        resp = client.get('/api/v1/municipalities')
        assert resp.status_code == 200
        data = resp.get_json()
        assert "municipalities" in data
        assert data["count"] == 2

    @patch('api_public.db')
    def test_get_municipality(self, mock_db, client):
        mock_db.get_municipality_profile.return_value = MOCK_MUNICIPALITY_PROFILE
        resp = client.get('/api/v1/municipalities/261')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["bfs_number"] == 261
        assert data["name"] == "Dietikon"

    @patch('api_public.db')
    def test_get_municipality_not_found(self, mock_db, client):
        mock_db.get_municipality_profile.return_value = None
        resp = client.get('/api/v1/municipalities/999')
        assert resp.status_code == 404

    @patch('api_public.db')
    def test_get_tariffs(self, mock_db, client):
        mock_db.get_elcom_tariffs.return_value = MOCK_ELCOM_TARIFFS
        resp = client.get('/api/v1/municipalities/261/tariffs')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 2
        assert data["tariffs"][0]["operator_name"] == "EKZ"

    @patch('api_public.db')
    def test_get_solar(self, mock_db, client):
        mock_db.get_sonnendach_municipal.return_value = MOCK_SONNENDACH
        resp = client.get('/api/v1/municipalities/261/solar')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["bfs_number"] == 261
        assert data["potential_kwp"] == 180000.0

    @patch('api_public.db')
    def test_get_solar_not_found(self, mock_db, client):
        mock_db.get_sonnendach_municipal.return_value = None
        resp = client.get('/api/v1/municipalities/999/solar')
        assert resp.status_code == 404


class TestScoreEndpoint:

    @patch('api_public.db')
    def test_score_breakdown(self, mock_db, client):
        mock_db.get_municipality_profile.return_value = MOCK_MUNICIPALITY_PROFILE
        resp = client.get('/api/v1/municipalities/261/score')
        assert resp.status_code == 200
        data = resp.get_json()
        assert "breakdown" in data
        assert "total_score" in data
        assert data["total_score"] > 0


class TestLegPotentialEndpoint:

    @patch('api_public.db')
    def test_leg_potential(self, mock_db, client):
        mock_db.get_elcom_tariffs.return_value = MOCK_ELCOM_TARIFFS
        resp = client.get('/api/v1/municipalities/261/leg-potential')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["annual_savings_chf"] > 0
        assert data["total_community_savings_chf"] > 0

    @patch('api_public.db')
    def test_leg_potential_no_tariff(self, mock_db, client):
        mock_db.get_elcom_tariffs.return_value = []
        resp = client.get('/api/v1/municipalities/261/leg-potential')
        assert resp.status_code == 404


class TestSearchEndpoint:

    @patch('api_public.db')
    def test_search(self, mock_db, client):
        mock_db.get_all_municipality_profiles.return_value = MOCK_PROFILES_LIST
        resp = client.get('/api/v1/search?q=Dietikon')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 1
        assert data["results"][0]["name"] == "Dietikon"

    @patch('api_public.db')
    def test_search_no_query(self, mock_db, client):
        resp = client.get('/api/v1/search?q=')
        assert resp.status_code == 400

    @patch('api_public.db')
    def test_search_no_results(self, mock_db, client):
        mock_db.get_all_municipality_profiles.return_value = MOCK_PROFILES_LIST
        resp = client.get('/api/v1/search?q=Nonexistent')
        data = resp.get_json()
        assert data["count"] == 0


class TestRankingsEndpoint:

    @patch('api_public.db')
    def test_rankings(self, mock_db, client):
        mock_db.get_all_municipality_profiles.return_value = MOCK_PROFILES_LIST
        resp = client.get('/api/v1/rankings?metric=energy_transition_score')
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["rankings"]) == 2
        assert data["rankings"][0]["rank"] == 1


class TestLegToolkitEndpoints:

    @patch('api_public.db')
    def test_value_gap_post(self, mock_db, client):
        mock_db.get_elcom_tariffs.return_value = MOCK_ELCOM_TARIFFS
        resp = client.post('/api/v1/leg/value-gap',
                           json={"bfs_number": 261, "num_participants": 20, "avg_consumption_kwh": 5000})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["annual_savings_per_household"] > 0
        assert data["total_community_savings"] > 0

    @patch('api_public.db')
    def test_value_gap_no_bfs(self, mock_db, client):
        resp = client.post('/api/v1/leg/value-gap', json={})
        assert resp.status_code == 400

    @patch('api_public.db')
    def test_financial_model(self, mock_db, client):
        resp = client.post('/api/v1/leg/financial-model',
                           json={"bfs_number": 261, "scenario": {"community_size": 10, "pv_kwp": 30, "consumption_kwh": 4500}})
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["projections"]) == 10
        assert data["projections"][0]["year"] == 1
        assert data["co2_reduction_kg_year"] > 0

    def test_templates(self, client):
        resp = client.get('/api/v1/leg/templates')
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["contracts"]) == 3


class TestCorsHeaders:

    def test_cors_origin(self, client):
        resp = client.get('/api/v1/search?q=test')
        assert resp.headers.get('Access-Control-Allow-Origin') == '*'
