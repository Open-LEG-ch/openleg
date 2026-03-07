"""Tests for formation document generation + DeepSign wiring."""

from unittest.mock import MagicMock, patch

from formation_wizard import generate_documents

MOCK_COMMUNITY_ROW = {
    'community_id': 'c1',
    'name': 'LEG Sonnenhof',
    'admin_building_id': 'b1',
    'distribution_model': 'simple',
    'description': '',
    'status': 'formation_started',
    'created_at': None,
    'updated_at': None,
    'formation_started_at': None,
    'dso_submitted_at': None,
    'dso_approved_at': None,
    'activated_at': None,
    'members': [
        {'building_id': 'b1', 'role': 'admin', 'status': 'confirmed', 'email': 'a@test.ch', 'address': 'Hauptstr 1'},
        {'building_id': 'b2', 'role': 'member', 'status': 'confirmed', 'email': 'b@test.ch', 'address': 'Hauptstr 2'},
        {'building_id': 'b3', 'role': 'member', 'status': 'confirmed', 'email': 'c@test.ch', 'address': 'Hauptstr 3'},
    ],
}


class TestGenerateDocuments:
    """generate_documents produces PDFs and calls DeepSign."""

    def _make_mock_db(self, community_row=None):
        mock_db = MagicMock()
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_db.get_connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db.get_connection.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cur.fetchone.return_value = community_row
        mock_db.store_leg_document.return_value = 1
        mock_db.track_event = MagicMock()
        return mock_db, mock_cur

    @patch('formation_wizard.os.getenv', return_value='')
    def test_generate_docs_returns_documents(self, mock_env):
        mock_db, mock_cur = self._make_mock_db(MOCK_COMMUNITY_ROW)
        result = generate_documents(mock_db, 'c1')
        assert result is not None
        assert 'community_agreement' in result
        assert 'participant_contracts' in result
        assert 'dso_notification' in result

    @patch('formation_wizard.os.getenv', return_value='')
    def test_generate_docs_stores_pdfs(self, mock_env):
        mock_db, mock_cur = self._make_mock_db(MOCK_COMMUNITY_ROW)
        result = generate_documents(mock_db, 'c1')
        assert result is not None
        # Should call store_leg_document for agreement + per-member contracts + dso
        assert mock_db.store_leg_document.call_count >= 3

    @patch('formation_wizard.os.getenv', return_value='')
    def test_generate_docs_pdf_bytes_are_valid(self, mock_env):
        mock_db, mock_cur = self._make_mock_db(MOCK_COMMUNITY_ROW)
        result = generate_documents(mock_db, 'c1')
        assert result is not None
        # Check store_leg_document was called with bytes starting with %PDF
        for call in mock_db.store_leg_document.call_args_list:
            pdf_bytes = call[0][2]  # 3rd positional arg
            assert isinstance(pdf_bytes, bytes)
            assert pdf_bytes[:5] == b'%PDF-'

    @patch('deepsign_integration.upload_document', return_value='ds-doc-1')
    @patch('deepsign_integration.request_signatures', return_value={'id': 'sr-1', 'status': 'pending'})
    @patch('formation_wizard.os.getenv', return_value='fake-key')
    def test_generate_docs_calls_deepsign(self, mock_env, mock_sign, mock_upload):
        mock_db, mock_cur = self._make_mock_db(MOCK_COMMUNITY_ROW)
        result = generate_documents(mock_db, 'c1')
        assert result is not None
        assert mock_upload.call_count >= 1
        assert mock_sign.call_count >= 1

    @patch('formation_wizard.os.getenv', return_value='')
    def test_generate_docs_skips_deepsign_without_key(self, mock_env):
        mock_db, mock_cur = self._make_mock_db(MOCK_COMMUNITY_ROW)
        with patch('deepsign_integration.upload_document') as mock_upload:
            result = generate_documents(mock_db, 'c1')
            assert result is not None
            mock_upload.assert_not_called()

    @patch('formation_wizard.os.getenv', return_value='')
    def test_generate_docs_updates_status(self, mock_env):
        mock_db, mock_cur = self._make_mock_db(MOCK_COMMUNITY_ROW)
        result = generate_documents(mock_db, 'c1')
        assert result is not None
        # Should update community status
        calls = [str(c) for c in mock_cur.execute.call_args_list]
        status_updates = [c for c in calls if 'documents_generated' in c or 'signatures_pending' in c]
        assert len(status_updates) >= 1

    def test_generate_docs_returns_none_for_missing_community(self):
        mock_db, mock_cur = self._make_mock_db(None)
        result = generate_documents(mock_db, 'nonexistent')
        assert result is None
