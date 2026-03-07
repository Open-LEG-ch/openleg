"""Tests for billing cron + endpoints wiring."""

import os
from datetime import datetime
from unittest.mock import patch

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MOCK_READINGS = [
    {'timestamp': datetime(2026, 2, 1, 0, 0), 'consumption_kwh': 1.0, 'production_kwh': 0.5},
    {'timestamp': datetime(2026, 2, 1, 0, 15), 'consumption_kwh': 1.2, 'production_kwh': 0.6},
]


class TestBillingCronAuth:
    def test_billing_route_exists(self):
        with open(os.path.join(PROJECT_ROOT, 'app.py')) as f:
            content = f.read()
        assert '/api/cron/process-billing' in content

    def test_billing_summary_route_exists(self):
        with open(os.path.join(PROJECT_ROOT, 'app.py')) as f:
            content = f.read()
        assert '/api/billing/community/' in content

    def test_cron_returns_403_without_secret(self, full_client):
        resp = full_client.post('/api/cron/process-billing')
        assert resp.status_code == 403


class TestBillingDBFunctions:
    def test_get_active_communities_exists(self):
        import database as db

        assert hasattr(db, 'get_active_communities')

    def test_get_community_for_building_exists(self):
        import database as db

        assert hasattr(db, 'get_community_for_building')

    def test_get_billing_period_exists(self):
        import database as db

        assert hasattr(db, 'get_billing_period')

    def test_get_community_member_building_ids_exists(self):
        import database as db

        assert hasattr(db, 'get_community_member_building_ids')


class TestBillingCronWiring:
    """Behavioral tests for billing cron endpoint."""

    @patch('billing_engine.generate_billing_summary')
    @patch('database.save_billing_period', return_value=1)
    @patch('database.get_meter_readings')
    @patch('database.get_community_member_building_ids', return_value=['b1', 'b2'])
    @patch('database.get_active_communities')
    def test_billing_cron_calls_engine(
        self, mock_communities, mock_members, mock_readings, mock_save, mock_engine, full_client
    ):
        mock_communities.return_value = [{'community_id': 'c1', 'distribution_model': 'proportional'}]
        mock_readings.return_value = MOCK_READINGS
        mock_engine.return_value = {
            'total_production_kwh': 1.1,
            'total_allocated_kwh': 0.8,
            'total_surplus_kwh': 0.3,
            'total_network_discount_chf': 0.05,
            'participants': [],
        }
        resp = full_client.post('/api/cron/process-billing', headers={'X-Cron-Secret': 'test-cron-secret'})
        assert resp.status_code == 200
        mock_engine.assert_called_once()

    @patch('billing_engine.generate_billing_summary')
    @patch('database.save_billing_period', return_value=1)
    @patch('database.get_meter_readings')
    @patch('database.get_community_member_building_ids')
    @patch('database.get_active_communities')
    def test_billing_cron_fetches_member_buildings(
        self, mock_communities, mock_members, mock_readings, mock_save, mock_engine, full_client
    ):
        mock_communities.return_value = [{'community_id': 'c1', 'distribution_model': 'proportional'}]
        mock_members.return_value = ['b1', 'b2']
        mock_readings.return_value = MOCK_READINGS
        mock_engine.return_value = {
            'total_production_kwh': 1.0,
            'total_allocated_kwh': 0.5,
            'total_surplus_kwh': 0.5,
            'total_network_discount_chf': 0.02,
            'participants': [],
        }
        resp = full_client.post('/api/cron/process-billing', headers={'X-Cron-Secret': 'test-cron-secret'})
        assert resp.status_code == 200
        mock_members.assert_called_once_with('c1')

    @patch('billing_engine.generate_billing_summary')
    @patch('database.save_billing_period', return_value=1)
    @patch('database.get_meter_readings')
    @patch('database.get_community_member_building_ids', return_value=['b1'])
    @patch('database.get_active_communities')
    def test_billing_cron_builds_dataframes(
        self, mock_communities, mock_members, mock_readings, mock_save, mock_engine, full_client
    ):
        mock_communities.return_value = [{'community_id': 'c1', 'distribution_model': 'proportional'}]
        mock_readings.return_value = MOCK_READINGS
        mock_engine.return_value = {
            'total_production_kwh': 1.0,
            'total_allocated_kwh': 1.0,
            'total_surplus_kwh': 0,
            'total_network_discount_chf': 0.04,
            'participants': [],
        }
        resp = full_client.post('/api/cron/process-billing', headers={'X-Cron-Secret': 'test-cron-secret'})
        assert resp.status_code == 200
        args = mock_engine.call_args
        import pandas as pd

        assert isinstance(args.args[0], pd.Series)
        assert isinstance(args.args[1], pd.DataFrame)

    @patch('billing_engine.generate_billing_summary')
    @patch('database.save_billing_period')
    @patch('database.get_meter_readings')
    @patch('database.get_community_member_building_ids', return_value=['b1'])
    @patch('database.get_active_communities')
    def test_billing_cron_saves_period(
        self, mock_communities, mock_members, mock_readings, mock_save, mock_engine, full_client
    ):
        mock_communities.return_value = [{'community_id': 'c1', 'distribution_model': 'proportional'}]
        mock_readings.return_value = MOCK_READINGS
        mock_engine.return_value = {
            'total_production_kwh': 0.5,
            'total_allocated_kwh': 0.5,
            'total_surplus_kwh': 0,
            'total_network_discount_chf': 0.02,
            'participants': [],
        }
        full_client.post('/api/cron/process-billing', headers={'X-Cron-Secret': 'test-cron-secret'})
        mock_save.assert_called_once()
        args = mock_save.call_args
        assert args.args[0] == 'c1'

    @patch('billing_engine.generate_billing_summary')
    @patch('database.save_billing_period', return_value=1)
    @patch('database.get_meter_readings')
    @patch('database.get_community_member_building_ids', return_value=['b1'])
    @patch('database.get_active_communities')
    def test_billing_cron_returns_count(
        self, mock_communities, mock_members, mock_readings, mock_save, mock_engine, full_client
    ):
        mock_communities.return_value = [{'community_id': 'c1', 'distribution_model': 'proportional'}]
        mock_readings.return_value = MOCK_READINGS
        mock_engine.return_value = {
            'total_production_kwh': 0.5,
            'total_allocated_kwh': 0.5,
            'total_surplus_kwh': 0,
            'total_network_discount_chf': 0.02,
            'participants': [],
        }
        resp = full_client.post('/api/cron/process-billing', headers={'X-Cron-Secret': 'test-cron-secret'})
        data = resp.get_json()
        assert data['processed'] == 1
        assert data['communities'] == 1

    @patch('database.get_meter_readings', return_value=[])
    @patch('database.get_community_member_building_ids', return_value=['b1'])
    @patch('database.get_active_communities')
    def test_billing_cron_skips_no_meter_data(self, mock_communities, mock_members, mock_readings, full_client):
        mock_communities.return_value = [{'community_id': 'c1', 'distribution_model': 'proportional'}]
        resp = full_client.post('/api/cron/process-billing', headers={'X-Cron-Secret': 'test-cron-secret'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['processed'] == 0
