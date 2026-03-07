"""Tests for public_data.py: fetchers and computed metrics."""

from unittest.mock import MagicMock, patch


class TestComputeValueGap:
    """Test LEG value-gap calculation."""

    def test_basic_value_gap(self):
        from public_data import compute_leg_value_gap

        h4 = {'grid_rp_kwh': 9.5, 'total_rp_kwh': 27.5}
        result = compute_leg_value_gap(h4, grid_reduction_pct=40.0)
        assert result['annual_savings_chf'] > 0
        assert result['monthly_savings_chf'] > 0
        assert result['savings_pct'] > 0
        assert result['grid_fee_rp_kwh'] == 9.5
        # 9.5 * 0.4 = 3.8 Rp/kWh * 4500 kWh / 100 = 171 CHF
        assert result['annual_savings_chf'] == 171.0

    def test_zero_grid_fee(self):
        from public_data import compute_leg_value_gap

        result = compute_leg_value_gap({'grid_rp_kwh': 0, 'total_rp_kwh': 27.0})
        assert result['annual_savings_chf'] == 0

    def test_empty_tariff(self):
        from public_data import compute_leg_value_gap

        result = compute_leg_value_gap({})
        assert result['annual_savings_chf'] == 0

    def test_ne5_reduction(self):
        from public_data import compute_leg_value_gap

        h4 = {'grid_rp_kwh': 9.5, 'total_rp_kwh': 27.5}
        result = compute_leg_value_gap(h4, grid_reduction_pct=25.0)
        # 9.5 * 0.25 = 2.375 * 4500 / 100 = 106.875
        assert result['annual_savings_chf'] == 106.88


class TestComputeTransitionScore:
    """Test energy transition score computation."""

    def test_full_score(self):
        from public_data import compute_energy_transition_score

        profile = {
            'solar_potential_pct': 100,
            'ev_share_pct': 30,
            'renewable_heating_pct': 100,
            'electricity_consumption_mwh': 100,
            'renewable_production_mwh': 100,
        }
        score = compute_energy_transition_score(profile)
        assert score == 100.0

    def test_zero_score(self):
        from public_data import compute_energy_transition_score

        score = compute_energy_transition_score({})
        assert score == 0.0

    def test_partial_score(self):
        from public_data import compute_energy_transition_score

        profile = {
            'solar_potential_pct': 50,
            'ev_share_pct': 15,
            'renewable_heating_pct': 50,
            'electricity_consumption_mwh': 200,
            'renewable_production_mwh': 50,
        }
        score = compute_energy_transition_score(profile)
        assert 0 < score < 100
        # solar: 50/100 * 30 = 15
        # ev: 15/30 * 20 = 10
        # heating: 50/100 * 25 = 12.5
        # prod: 50/200 * 25 = 6.25
        assert score == 43.8  # 15 + 10 + 12.5 + 6.25 rounded


class TestFetchElcom:
    """Test ElCom SPARQL fetcher (mocked HTTP)."""

    @patch('public_data.requests.post')
    def test_fetch_success(self, mock_post):
        from public_data import fetch_elcom_tariffs

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            'results': {
                'bindings': [
                    {
                        'operator': {'value': 'EKZ'},
                        'category': {'value': 'H4'},
                        'total': {'value': '27.5'},
                        'energy': {'value': '12.0'},
                        'grid': {'value': '9.5'},
                    }
                ]
            }
        }
        mock_post.return_value = mock_resp
        result = fetch_elcom_tariffs(261, 2026)
        assert len(result) == 1
        assert result[0]['operator_name'] == 'EKZ'
        assert result[0]['total_rp_kwh'] == 27.5

    @patch('public_data.requests.post')
    def test_fetch_empty(self, mock_post):
        from public_data import fetch_elcom_tariffs

        mock_resp = MagicMock()
        mock_resp.json.return_value = {'results': {'bindings': []}}
        mock_post.return_value = mock_resp
        assert fetch_elcom_tariffs(999, 2026) == []

    @patch('public_data.requests.post')
    def test_fetch_network_error(self, mock_post):
        from public_data import fetch_elcom_tariffs

        mock_post.side_effect = Exception('Connection timeout')
        assert fetch_elcom_tariffs(261, 2026) == []


class TestRefreshCanton:
    """Test refresh_canton ZH union bug and canton filtering."""

    @patch('database.save_municipality_profile')
    @patch('database.save_sonnendach_municipal')
    @patch('database.save_elcom_tariffs')
    @patch('public_data.fetch_elcom_tariffs', return_value=[])
    @patch('public_data.fetch_sonnendach_municipal', return_value=[])
    @patch('public_data.fetch_energie_reporter')
    def test_refresh_canton_zh_includes_zh_bfs(
        self, mock_er, mock_sd, mock_elcom, mock_save_elcom, mock_save_sd, mock_save_prof
    ):
        from public_data import ZH_BFS_NUMBERS, refresh_canton

        mock_er.return_value = [{'bfs_number': 261, 'kanton': 'ZH', 'name': 'Dietikon'}]
        refresh_canton('ZH')
        saved_bfs = {call.args[0]['bfs_number'] for call in mock_save_prof.call_args_list}
        for bfs in ZH_BFS_NUMBERS:
            assert bfs in saved_bfs, f'BFS {bfs} missing from ZH refresh'

    @patch('database.save_municipality_profile')
    @patch('database.save_sonnendach_municipal')
    @patch('database.save_elcom_tariffs')
    @patch('public_data.fetch_elcom_tariffs', return_value=[])
    @patch('public_data.fetch_sonnendach_municipal', return_value=[])
    @patch('public_data.fetch_energie_reporter')
    def test_refresh_canton_non_zh_excludes_zh_bfs(
        self, mock_er, mock_sd, mock_elcom, mock_save_elcom, mock_save_sd, mock_save_prof
    ):
        from public_data import ZH_BFS_NUMBERS, refresh_canton

        mock_er.return_value = [{'bfs_number': 351, 'kanton': 'BE', 'name': 'Bern'}]
        refresh_canton('BE')
        saved_bfs = {call.args[0]['bfs_number'] for call in mock_save_prof.call_args_list}
        for bfs in ZH_BFS_NUMBERS:
            assert bfs not in saved_bfs, f'ZH BFS {bfs} should not appear in BE refresh'
        assert 351 in saved_bfs

    @patch('database.save_municipality_profile')
    @patch('database.save_sonnendach_municipal')
    @patch('database.save_elcom_tariffs')
    @patch('public_data.fetch_elcom_tariffs', return_value=[])
    @patch('public_data.fetch_sonnendach_municipal')
    @patch('public_data.fetch_energie_reporter', return_value=[])
    def test_refresh_canton_saves_all_sonnendach(
        self, mock_er, mock_sd, mock_elcom, mock_save_elcom, mock_save_sd, mock_save_prof
    ):
        from public_data import refresh_canton

        mock_sd.return_value = [
            {'bfs_number': 261, 'potential_kwp': 100},
            {'bfs_number': 351, 'potential_kwp': 200},
        ]
        refresh_canton('ZH')
        sd_saved = [call.args[0]['bfs_number'] for call in mock_save_sd.call_args_list]
        assert 261 in sd_saved
        assert 351 in sd_saved  # saved even though not ZH


class TestRefreshAllMunicipalities:
    """Test bulk refresh for all Swiss municipalities."""

    def test_refresh_all_municipalities_exists(self):
        from public_data import refresh_all_municipalities

        assert callable(refresh_all_municipalities)

    @patch('database.save_municipality_profile')
    @patch('database.save_sonnendach_municipal')
    @patch('public_data.fetch_sonnendach_municipal')
    @patch('public_data.fetch_energie_reporter')
    def test_refresh_all_uses_bulk_no_elcom(self, mock_er, mock_sd, mock_save_sd, mock_save_prof):
        from public_data import refresh_all_municipalities

        mock_er.return_value = [
            {'bfs_number': 261, 'kanton': 'ZH', 'name': 'Dietikon', 'solar_potential_pct': 45},
            {'bfs_number': 351, 'kanton': 'BE', 'name': 'Bern', 'solar_potential_pct': 40},
        ]
        mock_sd.return_value = [
            {'bfs_number': 261, 'potential_kwp': 100},
        ]
        result = refresh_all_municipalities()
        assert result['municipalities'] >= 2
        assert result.get('elcom_calls', 0) == 0

    @patch('database.save_municipality_profile')
    @patch('database.save_sonnendach_municipal')
    @patch('public_data.fetch_sonnendach_municipal', return_value=[])
    @patch('public_data.fetch_energie_reporter')
    def test_refresh_all_computes_transition_score(self, mock_er, mock_sd, mock_save_sd, mock_save_prof):
        from public_data import refresh_all_municipalities

        mock_er.return_value = [
            {
                'bfs_number': 261,
                'kanton': 'ZH',
                'name': 'Dietikon',
                'solar_potential_pct': 50,
                'ev_share_pct': 15,
                'renewable_heating_pct': 50,
                'electricity_consumption_mwh': 200,
                'renewable_production_mwh': 50,
            },
        ]
        refresh_all_municipalities()
        saved = mock_save_prof.call_args_list[0].args[0]
        assert 'energy_transition_score' in saved
        assert saved['energy_transition_score'] > 0


class TestCronScope:
    """Test cron route scope parameter dispatch."""

    @patch('public_data.refresh_all_municipalities', return_value={'scope': 'all', 'municipalities': 100})
    @patch('public_data.refresh_canton', return_value={'kanton': 'ZH', 'municipalities': 11})
    def test_cron_accepts_scope_all(self, mock_canton, mock_all, full_client):
        resp = full_client.post(
            '/api/cron/refresh-public-data', headers={'X-Cron-Secret': 'test-cron-secret'}, json={'scope': 'all'}
        )
        assert resp.status_code == 200
        mock_all.assert_called_once()
        mock_canton.assert_not_called()

    @patch('public_data.refresh_all_municipalities', return_value={'scope': 'all', 'municipalities': 100})
    @patch('public_data.refresh_canton', return_value={'kanton': 'ZH', 'municipalities': 11})
    def test_cron_default_scope_zh(self, mock_canton, mock_all, full_client):
        resp = full_client.post('/api/cron/refresh-public-data', headers={'X-Cron-Secret': 'test-cron-secret'})
        assert resp.status_code == 200
        mock_canton.assert_called_once_with('ZH', year=2026)
        mock_all.assert_not_called()


class TestSafeHelpers:
    def test_safe_int(self):
        from public_data import _safe_int

        assert _safe_int('42') == 42
        assert _safe_int(42) == 42
        assert _safe_int(None) is None
        assert _safe_int('abc') is None

    def test_safe_float(self):
        from public_data import _safe_float

        assert _safe_float('3.14') == 3.14
        assert _safe_float('3,14') == 3.14
        assert _safe_float(None) is None
        assert _safe_float('abc') is None
