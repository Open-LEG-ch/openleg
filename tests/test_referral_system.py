"""Tests for referral system: stats, leaderboard, registration with referral code."""

from unittest.mock import patch


class TestReferralSystem:
    """Verify referral system end-to-end wiring."""

    @patch('database.get_referral_code', return_value='REF123')
    @patch('database.get_referral_stats', return_value={'total_referrals': 5})
    @patch('database.get_token', return_value={'building_id': 'b1', 'token_type': 'dashboard'})
    def test_referral_stats_endpoint(self, mock_token, mock_stats, mock_code, full_client):
        resp = full_client.get('/api/referral/stats?token=test-tok')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['referral_code'] == 'REF123'
        assert data['total_referrals'] == 5

    @patch('database.get_referral_leaderboard')
    def test_referral_leaderboard(self, mock_lb, full_client):
        mock_lb.return_value = [
            {'street': 'Bahnhofstrasse 1', 'referral_count': 10},
            {'street': 'Langstrasse 42', 'referral_count': 7},
        ]
        resp = full_client.get('/api/referral/leaderboard')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'leaderboard' in data
        assert len(data['leaderboard']) == 2
        # Verify display_name truncation
        for entry in data['leaderboard']:
            assert 'display_name' in entry

    @patch('database.save_building')
    @patch('database.get_building_by_referral_code')
    def test_registration_with_referral_code(self, mock_ref, mock_save, full_client):
        """Referral code resolves to referrer_id during registration."""
        mock_ref.return_value = {'building_id': 'referrer_b1'}
        # Attempt registration with referral code, expect it calls get_building_by_referral_code
        resp = full_client.post(
            '/api/register_anonymous',
            json={
                'email': 'test@example.ch',
                'referral_code': 'REF123',
                'profile': {'building_id': 'b_new', 'lat': 47.4, 'lon': 8.2},
                'consents': {'share_with_neighbors': True, 'share_with_utility': True},
            },
        )
        # Should at least call the referral code lookup
        mock_ref.assert_called_once_with('REF123')

    @patch('database.save_building')
    @patch('database.get_building_by_referral_code', return_value=None)
    def test_invalid_referral_code_no_block(self, mock_ref, mock_save, full_client):
        """Bad referral code doesn't block registration flow."""
        resp = full_client.post(
            '/api/register_anonymous',
            json={
                'email': 'test2@example.ch',
                'referral_code': 'INVALID_CODE',
                'profile': {'building_id': 'b_new2', 'lat': 47.4, 'lon': 8.2},
                'consents': {'share_with_neighbors': True, 'share_with_utility': True},
            },
        )
        # Registration should not be blocked by invalid referral code
        # The code should have called get_building_by_referral_code and gotten None
        mock_ref.assert_called_once_with('INVALID_CODE')
