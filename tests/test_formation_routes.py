"""Tests for formation wizard HTTP routes."""

from unittest.mock import patch


class TestFormationRoutes:
    """Test formation lifecycle REST endpoints."""

    @patch('formation_wizard.create_community')
    def test_create_community(self, mock_create, full_client):
        mock_create.return_value = {'community_id': 'c1', 'name': 'Test LEG', 'status': 'interested', 'member_count': 1}
        resp = full_client.post(
            '/api/formation/create',
            json={'name': 'Test LEG', 'building_id': 'b1', 'distribution_model': 'proportional'},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['community_id'] == 'c1'
        mock_create.assert_called_once()

    def test_create_requires_name_and_building(self, full_client):
        resp = full_client.post('/api/formation/create', json={})
        assert resp.status_code == 400
        data = resp.get_json()
        assert 'error' in data

    @patch('formation_wizard.invite_member', return_value=True)
    def test_invite_member(self, mock_invite, full_client):
        resp = full_client.post(
            '/api/formation/invite', json={'community_id': 'c1', 'building_id': 'b2', 'invited_by': 'b1'}
        )
        assert resp.status_code == 200
        mock_invite.assert_called_once()

    @patch('formation_wizard.confirm_membership', return_value=True)
    def test_confirm_membership(self, mock_confirm, full_client):
        resp = full_client.post('/api/formation/confirm', json={'community_id': 'c1', 'building_id': 'b2'})
        assert resp.status_code == 200
        mock_confirm.assert_called_once()

    @patch('formation_wizard.start_formation', return_value=False)
    def test_start_formation_min_members(self, mock_start, full_client):
        resp = full_client.post('/api/formation/start', json={'community_id': 'c1'})
        assert resp.status_code == 400

    @patch('formation_wizard.start_formation', return_value=True)
    def test_start_formation_success(self, mock_start, full_client):
        resp = full_client.post('/api/formation/start', json={'community_id': 'c1'})
        assert resp.status_code == 200

    @patch('formation_wizard.generate_documents')
    def test_generate_docs(self, mock_docs, full_client):
        mock_docs.return_value = {
            'community_agreement': {'document_id': 'd1'},
            'participant_contracts': [],
            'dso_notification': {'document_id': 'd2'},
        }
        resp = full_client.post('/api/formation/generate-docs', json={'community_id': 'c1'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'community_agreement' in data

    @patch('formation_wizard.submit_to_dso', return_value=True)
    def test_submit_dso(self, mock_dso, full_client):
        resp = full_client.post('/api/formation/submit-dso', json={'community_id': 'c1'})
        assert resp.status_code == 200

    @patch('formation_wizard.get_community_status')
    def test_get_status(self, mock_status, full_client):
        mock_status.return_value = {
            'community_id': 'c1',
            'name': 'Test LEG',
            'status': 'interested',
            'member_count': {'total': 3, 'confirmed': 3},
        }
        resp = full_client.get('/api/formation/status/c1')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['community_id'] == 'c1'

    @patch('formation_wizard.get_community_status', return_value=None)
    def test_status_not_found(self, mock_status, full_client):
        resp = full_client.get('/api/formation/status/nonexistent')
        assert resp.status_code == 404
