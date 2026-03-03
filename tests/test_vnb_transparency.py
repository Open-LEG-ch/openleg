"""Tests for VNB transparency scoring (P5)."""
import pytest
from unittest.mock import patch, MagicMock


# === Unit tests for scoring function ===

class TestVnbTransparencyScore:
    """compute_vnb_transparency_score produces 0-100 scores."""

    def test_full_data_scores_high(self):
        from public_data import compute_vnb_transparency_score
        tariffs = [
            {"operator_name": "EKZ", "category": "H1", "total_rp_kwh": 32.0,
             "energy_rp_kwh": 14.0, "grid_rp_kwh": 11.0, "municipality_fee_rp_kwh": 4.0, "kev_rp_kwh": 3.0},
            {"operator_name": "EKZ", "category": "H2", "total_rp_kwh": 30.0,
             "energy_rp_kwh": 13.0, "grid_rp_kwh": 10.0, "municipality_fee_rp_kwh": 4.0, "kev_rp_kwh": 3.0},
            {"operator_name": "EKZ", "category": "H3", "total_rp_kwh": 28.0,
             "energy_rp_kwh": 12.0, "grid_rp_kwh": 9.5, "municipality_fee_rp_kwh": 3.5, "kev_rp_kwh": 3.0},
            {"operator_name": "EKZ", "category": "H4", "total_rp_kwh": 27.5,
             "energy_rp_kwh": 12.0, "grid_rp_kwh": 9.5, "municipality_fee_rp_kwh": 3.0, "kev_rp_kwh": 3.0},
        ]
        score = compute_vnb_transparency_score(tariffs, municipalities_served=50)
        assert 60 <= score <= 100

    def test_missing_components_scores_lower(self):
        from public_data import compute_vnb_transparency_score
        tariffs = [
            {"operator_name": "SmallDSO", "category": "H4", "total_rp_kwh": 27.5,
             "energy_rp_kwh": None, "grid_rp_kwh": None, "municipality_fee_rp_kwh": None, "kev_rp_kwh": None},
        ]
        score = compute_vnb_transparency_score(tariffs, municipalities_served=1)
        assert score < 50

    def test_empty_tariffs_scores_zero(self):
        from public_data import compute_vnb_transparency_score
        score = compute_vnb_transparency_score([], municipalities_served=0)
        assert score == 0

    def test_score_range_0_100(self):
        from public_data import compute_vnb_transparency_score
        tariffs = [
            {"operator_name": "X", "category": "H4", "total_rp_kwh": 25.0,
             "energy_rp_kwh": 10.0, "grid_rp_kwh": 8.0, "municipality_fee_rp_kwh": 4.0, "kev_rp_kwh": 3.0},
        ]
        score = compute_vnb_transparency_score(tariffs, municipalities_served=5)
        assert 0 <= score <= 100


# === API endpoint test ===

class TestVnbRankingsAPI:
    """GET /api/v1/vnb/rankings returns scored DSO list."""

    def test_vnb_rankings_returns_200(self, client):
        with patch('database.get_all_municipality_profiles', return_value=[
            {"bfs_number": 261, "name": "Dietikon", "kanton": "ZH"},
        ]), patch('database.get_elcom_tariffs', return_value=[
            {"operator_name": "EKZ", "category": "H4", "total_rp_kwh": 27.5,
             "energy_rp_kwh": 12.0, "grid_rp_kwh": 9.5, "municipality_fee_rp_kwh": 3.0, "kev_rp_kwh": 3.0},
        ]):
            resp = client.get('/api/v1/vnb/rankings')
            assert resp.status_code == 200
            data = resp.get_json()
            assert 'rankings' in data

    def test_vnb_rankings_has_scores(self, client):
        with patch('database.get_all_municipality_profiles', return_value=[
            {"bfs_number": 261, "name": "Dietikon", "kanton": "ZH"},
            {"bfs_number": 247, "name": "Schlieren", "kanton": "ZH"},
        ]), patch('database.get_elcom_tariffs', return_value=[
            {"operator_name": "EKZ", "category": "H4", "total_rp_kwh": 27.5,
             "energy_rp_kwh": 12.0, "grid_rp_kwh": 9.5, "municipality_fee_rp_kwh": 3.0, "kev_rp_kwh": 3.0},
        ]):
            resp = client.get('/api/v1/vnb/rankings')
            data = resp.get_json()
            for entry in data['rankings']:
                assert 'operator_name' in entry
                assert 'transparency_score' in entry
                assert 0 <= entry['transparency_score'] <= 100

    def test_vnb_rankings_sorted_descending(self, client):
        with patch('database.get_all_municipality_profiles', return_value=[
            {"bfs_number": 261, "name": "Dietikon", "kanton": "ZH"},
        ]), patch('database.get_elcom_tariffs', return_value=[
            {"operator_name": "EKZ", "category": "H4", "total_rp_kwh": 27.5,
             "energy_rp_kwh": 12.0, "grid_rp_kwh": 9.5, "municipality_fee_rp_kwh": 3.0, "kev_rp_kwh": 3.0},
            {"operator_name": "EKZ", "category": "H1", "total_rp_kwh": 32.0,
             "energy_rp_kwh": 14.0, "grid_rp_kwh": 11.0, "municipality_fee_rp_kwh": 4.0, "kev_rp_kwh": 3.0},
        ]):
            resp = client.get('/api/v1/vnb/rankings')
            data = resp.get_json()
            scores = [e['transparency_score'] for e in data['rankings']]
            assert scores == sorted(scores, reverse=True)

    def test_vnb_rankings_empty(self, client):
        with patch('database.get_all_municipality_profiles', return_value=[]), \
             patch('database.get_elcom_tariffs', return_value=[]):
            resp = client.get('/api/v1/vnb/rankings')
            assert resp.status_code == 200
            assert resp.get_json()['rankings'] == []
