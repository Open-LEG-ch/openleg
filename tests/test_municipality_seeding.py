"""Tests for bulk municipality seeding (P1: seed all 2131 Swiss municipalities)."""

from unittest.mock import patch

MOCK_ENERGIE_REPORTER = [
    {
        'bfs_number': 261,
        'name': 'Dietikon',
        'kanton': 'ZH',
        'solar_potential_pct': 45.0,
        'ev_share_pct': 12.0,
        'renewable_heating_pct': 35.0,
        'electricity_consumption_mwh': 180000,
        'renewable_production_mwh': 25000,
    },
    {
        'bfs_number': 351,
        'name': 'Bern',
        'kanton': 'BE',
        'solar_potential_pct': 40.0,
        'ev_share_pct': 10.0,
        'renewable_heating_pct': 30.0,
        'electricity_consumption_mwh': 500000,
        'renewable_production_mwh': 60000,
    },
    {
        'bfs_number': 6621,
        'name': 'Lugano',
        'kanton': 'TI',
        'solar_potential_pct': 55.0,
        'ev_share_pct': 8.0,
        'renewable_heating_pct': 25.0,
        'electricity_consumption_mwh': 300000,
        'renewable_production_mwh': 40000,
    },
]


class TestSeedAllMunicipalities:
    """seed_all_municipalities creates DB records from Energie Reporter."""

    @patch('public_data.fetch_energie_reporter', return_value=MOCK_ENERGIE_REPORTER)
    @patch('database.save_municipality')
    @patch('database.save_municipality_profile')
    def test_seeds_municipalities(self, mock_profile, mock_save, mock_fetch):
        from municipality_seeder import seed_all_municipalities

        mock_save.return_value = 1
        mock_profile.return_value = True
        result = seed_all_municipalities()
        assert result['seeded'] == 3
        assert mock_save.call_count == 3
        assert mock_profile.call_count == 3

    @patch('public_data.fetch_energie_reporter', return_value=MOCK_ENERGIE_REPORTER)
    @patch('database.save_municipality')
    @patch('database.save_municipality_profile')
    def test_generates_subdomains(self, mock_profile, mock_save, mock_fetch):
        from municipality_seeder import seed_all_municipalities

        mock_save.return_value = 1
        mock_profile.return_value = True
        seed_all_municipalities()
        # Check subdomain generation for each call
        for c in mock_save.call_args_list:
            kwargs = c[1] if c[1] else {}
            args = c[0] if c[0] else ()
            # subdomain should be a slug
            subdomain = kwargs.get('subdomain') or (args[5] if len(args) > 5 else None)
            assert subdomain is not None
            assert subdomain == subdomain.lower()
            assert ' ' not in subdomain

    @patch('public_data.fetch_energie_reporter', return_value=MOCK_ENERGIE_REPORTER)
    @patch('database.save_municipality')
    @patch('database.save_municipality_profile')
    def test_computes_energy_score(self, mock_profile, mock_save, mock_fetch):
        from municipality_seeder import seed_all_municipalities

        mock_save.return_value = 1
        mock_profile.return_value = True
        seed_all_municipalities()
        # Each profile should have energy_transition_score
        for c in mock_profile.call_args_list:
            profile = c[0][0]
            assert 'energy_transition_score' in profile
            assert profile['energy_transition_score'] > 0

    @patch('public_data.fetch_energie_reporter', return_value=[])
    def test_empty_source_returns_zero(self, mock_fetch):
        from municipality_seeder import seed_all_municipalities

        result = seed_all_municipalities()
        assert result['seeded'] == 0

    @patch('public_data.fetch_energie_reporter', return_value=MOCK_ENERGIE_REPORTER)
    @patch('database.save_municipality', return_value=None)
    @patch('database.save_municipality_profile')
    def test_counts_failures(self, mock_profile, mock_save, mock_fetch):
        from municipality_seeder import seed_all_municipalities

        result = seed_all_municipalities()
        assert result['failed'] == 3
        assert result['seeded'] == 0


class TestSeedCronEndpoint:
    """Cron endpoint triggers seeding."""

    @patch('municipality_seeder.seed_all_municipalities', return_value={'seeded': 100, 'failed': 5})
    def test_cron_seed_requires_secret(self, mock_seed, full_client):
        resp = full_client.post('/api/cron/seed-municipalities')
        assert resp.status_code == 403

    @patch('municipality_seeder.seed_all_municipalities', return_value={'seeded': 100, 'failed': 5})
    def test_cron_seed_with_secret(self, mock_seed, full_client):
        resp = full_client.post('/api/cron/seed-municipalities', headers={'X-Cron-Secret': 'test-cron-secret'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['seeded'] == 100


class TestTenantProvisioning:
    """provision_tenant_for_municipality creates white_label_configs rows."""

    @patch('database.upsert_tenant', return_value=True)
    @patch('database.get_tenant_by_territory', return_value=None)
    def test_provision_tenant_creates_config(self, mock_get, mock_upsert):
        from municipality_seeder import provision_tenant_for_municipality

        result = provision_tenant_for_municipality(261, 'Dietikon', 'ZH', 'dietikon')
        assert result is True
        mock_upsert.assert_called_once()
        config = mock_upsert.call_args[0][1]
        assert config['city_name'] == 'Dietikon'
        assert config['kanton'] == 'ZH'
        assert config['kanton_code'] == 'ZH'
        assert config['active'] is True

    @patch('database.upsert_tenant', return_value=True)
    @patch('database.get_tenant_by_territory', return_value={'territory': 'dietikon'})
    def test_provision_tenant_skips_existing(self, mock_get, mock_upsert):
        from municipality_seeder import provision_tenant_for_municipality

        result = provision_tenant_for_municipality(261, 'Dietikon', 'ZH', 'dietikon')
        assert result is False
        mock_upsert.assert_not_called()

    @patch('database.upsert_tenant', return_value=True)
    @patch('database.get_tenant_by_territory', return_value=None)
    def test_provision_tenant_with_dso(self, mock_get, mock_upsert):
        from municipality_seeder import provision_tenant_for_municipality

        provision_tenant_for_municipality(261, 'Dietikon', 'ZH', 'dietikon', dso_name='EKZ')
        config = mock_upsert.call_args[0][1]
        assert config['utility_name'] == 'EKZ'
        assert 'EKZ' in config['dso_contact']

    @patch('public_data.fetch_energie_reporter', return_value=MOCK_ENERGIE_REPORTER)
    @patch('database.save_municipality', return_value=1)
    @patch('database.save_municipality_profile', return_value=True)
    @patch('database.upsert_tenant', return_value=True)
    @patch('database.get_tenant_by_territory', return_value=None)
    def test_seed_all_provisions_tenants_when_flag_set(
        self, mock_get_t, mock_upsert, mock_profile, mock_save, mock_fetch
    ):
        from municipality_seeder import seed_all_municipalities

        result = seed_all_municipalities(provision_tenants=True)
        assert result['seeded'] == 3
        assert result['tenants_created'] == 3
        assert mock_upsert.call_count == 3

    @patch('public_data.fetch_energie_reporter', return_value=MOCK_ENERGIE_REPORTER)
    @patch('database.save_municipality', return_value=1)
    @patch('database.save_municipality_profile', return_value=True)
    @patch('database.upsert_tenant', return_value=True)
    def test_seed_all_no_tenants_by_default(self, mock_upsert, mock_profile, mock_save, mock_fetch):
        from municipality_seeder import seed_all_municipalities

        seed_all_municipalities()
        mock_upsert.assert_not_called()

    @patch(
        'municipality_seeder.seed_all_municipalities',
        return_value={'seeded': 10, 'failed': 0, 'skipped': 0, 'tenants_created': 10},
    )
    def test_cron_seed_with_provision_tenants(self, mock_seed, full_client):
        resp = full_client.post(
            '/api/cron/seed-municipalities?provision_tenants=true', headers={'X-Cron-Secret': 'test-cron-secret'}
        )
        assert resp.status_code == 200
        mock_seed.assert_called_once()
        call_kwargs = mock_seed.call_args[1]
        assert call_kwargs.get('provision_tenants') is True
