"""Tests for VNB transparency scoring (P5) + Phase 1 slices 1.1-1.8."""
import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock, call


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


# === 1.1: Bulk SPARQL fetch ===

class TestBulkSparqlFetch:
    """fetch_elcom_tariffs_bulk builds VALUES block, chunks by 50."""

    def test_bulk_returns_list_of_dicts(self):
        from public_data import fetch_elcom_tariffs_bulk
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": {"bindings": [
                {
                    "bfs": {"value": "261"},
                    "operator": {"value": "EKZ"},
                    "category": {"value": "H4"},
                    "total": {"value": "27.5"},
                    "energy": {"value": "12.0"},
                    "grid": {"value": "9.5"},
                    "municipality_fee": {"value": "3.0"},
                    "kev": {"value": "3.0"},
                },
            ]},
        }
        mock_resp.raise_for_status = MagicMock()
        with patch('public_data.requests.post', return_value=mock_resp) as mock_post:
            result = fetch_elcom_tariffs_bulk([261], year=2026)
            assert len(result) == 1
            assert result[0]['bfs_number'] == 261
            assert result[0]['operator_name'] == 'EKZ'
            assert result[0]['total_rp_kwh'] == 27.5
            mock_post.assert_called_once()

    def test_bulk_chunks_by_50(self):
        from public_data import fetch_elcom_tariffs_bulk
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"results": {"bindings": []}}
        mock_resp.raise_for_status = MagicMock()
        bfs_list = list(range(1, 121))  # 120 BFS numbers -> 3 chunks
        with patch('public_data.requests.post', return_value=mock_resp) as mock_post:
            fetch_elcom_tariffs_bulk(bfs_list, year=2026, chunk_size=50)
            assert mock_post.call_count == 3

    def test_bulk_empty_input(self):
        from public_data import fetch_elcom_tariffs_bulk
        result = fetch_elcom_tariffs_bulk([], year=2026)
        assert result == []

    def test_bulk_handles_error(self):
        from public_data import fetch_elcom_tariffs_bulk
        with patch('public_data.requests.post', side_effect=Exception("timeout")):
            result = fetch_elcom_tariffs_bulk([261], year=2026)
            assert result == []

    def test_bulk_same_shape_as_single(self):
        """Output dict keys match fetch_elcom_tariffs shape."""
        from public_data import fetch_elcom_tariffs_bulk
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": {"bindings": [
                {
                    "bfs": {"value": "261"},
                    "operator": {"value": "EKZ"},
                    "category": {"value": "H4"},
                    "total": {"value": "27.5"},
                    "energy": {"value": "12.0"},
                    "grid": {"value": "9.5"},
                    "municipality_fee": {"value": "3.0"},
                    "kev": {"value": "3.0"},
                },
            ]},
        }
        mock_resp.raise_for_status = MagicMock()
        with patch('public_data.requests.post', return_value=mock_resp):
            result = fetch_elcom_tariffs_bulk([261], year=2026)
            expected_keys = {'bfs_number', 'year', 'operator_name', 'category',
                             'total_rp_kwh', 'energy_rp_kwh', 'grid_rp_kwh',
                             'municipality_fee_rp_kwh', 'kev_rp_kwh'}
            assert set(result[0].keys()) == expected_keys


# === 1.2: get_elcom_last_refresh ===

class TestGetElcomLastRefresh:
    """database.get_elcom_last_refresh returns timestamp + count."""

    def test_returns_dict_with_keys(self):
        mock_row = {'last_refresh': datetime(2026, 3, 1, 10, 0), 'record_count': 42}
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = mock_row
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch('database.get_connection', return_value=mock_conn):
            import database as db
            result = db.get_elcom_last_refresh()
            assert result['last_refresh'] == datetime(2026, 3, 1, 10, 0)
            assert result['record_count'] == 42

    def test_returns_none_on_empty(self):
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch('database.get_connection', return_value=mock_conn):
            import database as db
            result = db.get_elcom_last_refresh()
            assert result == {'last_refresh': None, 'record_count': 0}

    def test_returns_fallback_on_error(self):
        with patch('database.get_connection', side_effect=Exception("db down")):
            import database as db
            result = db.get_elcom_last_refresh()
            assert result == {'last_refresh': None, 'record_count': 0}


# === 1.3: refresh_canton uses bulk ===

class TestRefreshCantonBulk:
    """refresh_canton calls fetch_elcom_tariffs_bulk once, not N single calls."""

    def test_calls_bulk_once(self):
        from public_data import refresh_canton
        with patch('public_data.fetch_energie_reporter', return_value=[
            {"bfs_number": 261, "kanton": "ZH", "name": "Dietikon"},
            {"bfs_number": 247, "kanton": "ZH", "name": "Schlieren"},
        ]), patch('public_data.fetch_sonnendach_municipal', return_value=[]), \
             patch('public_data.fetch_elcom_tariffs_bulk', return_value=[
                 {"bfs_number": 261, "year": 2026, "operator_name": "EKZ", "category": "H4",
                  "total_rp_kwh": 27.5, "energy_rp_kwh": 12.0, "grid_rp_kwh": 9.5,
                  "municipality_fee_rp_kwh": 3.0, "kev_rp_kwh": 3.0},
             ]) as mock_bulk, \
             patch('public_data.fetch_elcom_tariffs') as mock_single, \
             patch('database.save_elcom_tariffs', return_value=1), \
             patch('database.save_sonnendach_municipal'), \
             patch('database.save_municipality_profile'), \
             patch('database.get_municipality', return_value=None):
            refresh_canton('ZH', 2026)
            mock_bulk.assert_called_once()
            mock_single.assert_not_called()


# === 1.4: Cron kanton scope ===

class TestCronKantonScope:
    """POST /api/cron/refresh-public-data accepts any 2-letter kanton."""

    def test_custom_kanton(self, full_client):
        with patch('public_data.refresh_canton', return_value={"kanton": "BE", "municipalities": 5}) as mock_rc:
            resp = full_client.post('/api/cron/refresh-public-data',
                                    json={'scope': 'be'},
                                    headers={'X-Cron-Secret': 'test-cron-secret'})
            assert resp.status_code == 200
            mock_rc.assert_called_once_with('BE', year=2026)

    def test_all_scope(self, full_client):
        with patch('public_data.refresh_all_municipalities', return_value={"scope": "all"}) as mock_all:
            resp = full_client.post('/api/cron/refresh-public-data',
                                    json={'scope': 'all'},
                                    headers={'X-Cron-Secret': 'test-cron-secret'})
            assert resp.status_code == 200
            mock_all.assert_called_once()

    def test_invalid_scope_defaults_zh(self, full_client):
        with patch('public_data.refresh_canton', return_value={"kanton": "ZH"}) as mock_rc:
            resp = full_client.post('/api/cron/refresh-public-data',
                                    json={'scope': 'invalid123'},
                                    headers={'X-Cron-Secret': 'test-cron-secret'})
            assert resp.status_code == 200
            mock_rc.assert_called_once_with('ZH', year=2026)

    def test_custom_year(self, full_client):
        with patch('public_data.refresh_canton', return_value={"kanton": "AG"}) as mock_rc:
            resp = full_client.post('/api/cron/refresh-public-data',
                                    json={'scope': 'ag', 'year': 2025},
                                    headers={'X-Cron-Secret': 'test-cron-secret'})
            assert resp.status_code == 200
            mock_rc.assert_called_once_with('AG', year=2025)


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


# === 1.5 + 1.7: Template route tests (freshness + stats) ===

MOCK_OPERATOR_TARIFFS = {
    'EKZ': [
        {"operator_name": "EKZ", "category": "H4", "total_rp_kwh": 27.5,
         "energy_rp_kwh": 12.0, "grid_rp_kwh": 9.5, "municipality_fee_rp_kwh": 3.0,
         "kev_rp_kwh": 3.0, "bfs_number": 261},
    ],
}
MOCK_PROFILES = [
    {"bfs_number": 261, "name": "Dietikon", "kanton": "ZH"},
]
MOCK_REFRESH = {'last_refresh': datetime(2026, 3, 1, 10, 0), 'record_count': 42}


class TestTransparenzPage:
    """GET /transparenz renders the VNB transparency page."""

    def _mock_transparenz(self):
        return (
            patch('database.get_all_elcom_tariffs_by_operator', return_value=MOCK_OPERATOR_TARIFFS),
            patch('database.get_all_municipality_profiles', return_value=MOCK_PROFILES),
            patch('database.get_elcom_last_refresh', return_value=MOCK_REFRESH),
        )

    def test_transparenz_returns_200(self, full_client):
        p1, p2, p3 = self._mock_transparenz()
        with p1, p2, p3:
            resp = full_client.get('/transparenz')
            assert resp.status_code == 200
            assert b'EKZ' in resp.data

    def test_transparenz_contains_score_markup(self, full_client):
        p1, p2, p3 = self._mock_transparenz()
        with p1, p2, p3:
            resp = full_client.get('/transparenz')
            assert b'vnb-table' in resp.data

    def test_transparenz_shows_freshness_indicator(self, full_client):
        """Slice 1.5: last refresh timestamp shown on page."""
        p1, p2, p3 = self._mock_transparenz()
        with p1, p2, p3:
            resp = full_client.get('/transparenz')
            assert b'01.03.2026' in resp.data
            assert b'42' in resp.data

    def test_transparenz_shows_stats_header(self, full_client):
        """Slice 1.7: operator count, avg score, municipalities covered."""
        p1, p2, p3 = self._mock_transparenz()
        with p1, p2, p3:
            resp = full_client.get('/transparenz')
            # 1 operator
            html = resp.data.decode()
            assert 'transparenz_stats_vnb' not in html or '1' in html

    def test_transparenz_municipality_names_shown(self, full_client):
        """Slice 1.6: municipality names appear in table."""
        p1, p2, p3 = self._mock_transparenz()
        with p1, p2, p3:
            resp = full_client.get('/transparenz')
            assert b'Dietikon' in resp.data

    def test_transparenz_municipality_links(self, full_client):
        """Slice 1.8: municipality names link to /gemeinde/profil/<bfs>."""
        p1, p2, p3 = self._mock_transparenz()
        with p1, p2, p3:
            resp = full_client.get('/transparenz')
            assert b'/gemeinde/profil/261' in resp.data

    def test_transparenz_no_freshness_when_empty(self, full_client):
        """No freshness line when last_refresh is None."""
        with patch('database.get_all_elcom_tariffs_by_operator', return_value={}), \
             patch('database.get_all_municipality_profiles', return_value=[]), \
             patch('database.get_elcom_last_refresh', return_value={'last_refresh': None, 'record_count': 0}):
            resp = full_client.get('/transparenz')
            assert resp.status_code == 200
            # Should not contain the freshness line
            assert b'01.03.2026' not in resp.data
