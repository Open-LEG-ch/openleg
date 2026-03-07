"""Tests for DSO submission wiring."""

from unittest.mock import MagicMock, patch

from formation_wizard import submit_to_dso

MOCK_COMMUNITY_ROW = {
    'community_id': 'c1',
    'name': 'LEG Sonnenhof',
    'status': 'signatures_pending',
    'admin_building_id': 'b1',
    'distribution_model': 'simple',
}

MOCK_BUILDING_ROW = {'city_id': 'dietikon'}

MOCK_LEG_DOCS = [
    {'id': 1, 'doc_type': 'community_agreement', 'filename': 'vereinbarung.pdf', 'signing_status': 'signed'},
    {'id': 2, 'doc_type': 'dso_notification', 'filename': 'vnb_anmeldung.pdf', 'signing_status': 'unsigned'},
]

MOCK_TENANT = {
    'dso_contact': 'vnb@ekz.ch',
    'utility_name': 'EKZ Verteilnetz AG',
    'territory': 'dietikon',
}


class TestDSOSubmission:
    """submit_to_dso sends email with attachments and updates status."""

    def _make_mock_db(self, community=None, docs=None, tenant=None, building=None):
        mock_db = MagicMock()
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_db.get_connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db.get_connection.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        # fetchone: 1st call = community, 2nd = building row
        mock_cur.fetchone.side_effect = [community, building or MOCK_BUILDING_ROW]
        mock_cur.rowcount = 1 if community else 0

        mock_db.list_leg_documents.return_value = docs or []
        mock_db.get_leg_document_pdf.return_value = b'%PDF-fake'
        mock_db.get_tenant_by_territory.return_value = tenant
        mock_db.track_event = MagicMock()
        return mock_db, mock_cur

    @patch('formation_wizard.send_dso_email', return_value=True)
    def test_submit_sends_email(self, mock_send):
        mock_db, cur = self._make_mock_db(MOCK_COMMUNITY_ROW, MOCK_LEG_DOCS, MOCK_TENANT)
        ok = submit_to_dso(mock_db, 'c1')
        assert ok is True
        mock_send.assert_called_once()
        args = mock_send.call_args
        assert args[0][0] == 'vnb@ekz.ch'

    @patch('formation_wizard.send_dso_email', return_value=True)
    def test_submit_updates_status(self, mock_send):
        mock_db, cur = self._make_mock_db(MOCK_COMMUNITY_ROW, MOCK_LEG_DOCS, MOCK_TENANT)
        ok = submit_to_dso(mock_db, 'c1')
        assert ok is True
        calls = [str(c) for c in cur.execute.call_args_list]
        assert any('dso_submitted' in c for c in calls)

    @patch('formation_wizard.send_dso_email', return_value=False)
    def test_submit_fails_if_email_fails(self, mock_send):
        mock_db, cur = self._make_mock_db(MOCK_COMMUNITY_ROW, MOCK_LEG_DOCS, MOCK_TENANT)
        ok = submit_to_dso(mock_db, 'c1')
        assert ok is False

    def test_submit_fails_without_community(self):
        mock_db, cur = self._make_mock_db(None)
        ok = submit_to_dso(mock_db, 'nonexistent')
        assert ok is False

    @patch('formation_wizard.send_dso_email', return_value=True)
    def test_submit_tracks_event(self, mock_send):
        mock_db, cur = self._make_mock_db(MOCK_COMMUNITY_ROW, MOCK_LEG_DOCS, MOCK_TENANT)
        ok = submit_to_dso(mock_db, 'c1')
        assert ok is True
        mock_db.track_event.assert_called()

    @patch('formation_wizard.send_dso_email', return_value=True)
    def test_submit_attaches_pdfs(self, mock_send):
        mock_db, cur = self._make_mock_db(MOCK_COMMUNITY_ROW, MOCK_LEG_DOCS, MOCK_TENANT)
        ok = submit_to_dso(mock_db, 'c1')
        assert ok is True
        args = mock_send.call_args
        attachments = args[0][3]
        assert len(attachments) == 2
        assert attachments[0]['filename'] == 'vereinbarung.pdf'

    @patch('formation_wizard.send_dso_email')
    def test_submit_no_dso_contact_still_succeeds(self, mock_send):
        """When no tenant/DSO contact, submission still succeeds (logged)."""
        mock_db, cur = self._make_mock_db(MOCK_COMMUNITY_ROW, MOCK_LEG_DOCS, None, {'city_id': 'x'})
        ok = submit_to_dso(mock_db, 'c1')
        assert ok is True
        mock_send.assert_not_called()
