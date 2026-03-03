"""Tests for LEA auto-follow-up stale outreach."""
import os
import pytest
from unittest.mock import patch, MagicMock


def test_get_stale_outreach_returns_unanswered():
    """get_stale_outreach should return approved outreach with no reply."""
    import database as db_mod
    mock_cur = MagicMock()
    mock_cur.fetchall.return_value = [
        {'request_id': 'r1', 'reference': 'muni-1', 'summary': 'outreach to X', 'decided_at': '2026-02-20'}
    ]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = lambda s: mock_cur
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    with patch("database.get_connection") as mock_gc:
        mock_gc.return_value.__enter__ = lambda s: mock_conn
        mock_gc.return_value.__exit__ = MagicMock(return_value=False)
        results = db_mod.get_stale_outreach(days_threshold=7)
        assert len(results) == 1
        assert results[0]['request_id'] == 'r1'


def test_get_stale_outreach_excludes_replied():
    """Outreach with reply events should be excluded."""
    import database as db_mod
    mock_cur = MagicMock()
    mock_cur.fetchall.return_value = []
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = lambda s: mock_cur
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    with patch("database.get_connection") as mock_gc:
        mock_gc.return_value.__enter__ = lambda s: mock_conn
        mock_gc.return_value.__exit__ = MagicMock(return_value=False)
        results = db_mod.get_stale_outreach(days_threshold=7)
        assert len(results) == 0


def test_get_stale_outreach_excludes_recent():
    """get_stale_outreach SQL should filter by days_threshold."""
    import database as db_mod
    mock_cur = MagicMock()
    mock_cur.fetchall.return_value = []
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = lambda s: mock_cur
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    with patch("database.get_connection") as mock_gc:
        mock_gc.return_value.__enter__ = lambda s: mock_conn
        mock_gc.return_value.__exit__ = MagicMock(return_value=False)
        db_mod.get_stale_outreach(days_threshold=14)
        sql = mock_cur.execute.call_args[0][0]
        assert '14' in str(mock_cur.execute.call_args) or 'interval' in sql.lower()


def test_followup_cron_exists_in_entrypoint():
    """entrypoint.sh should have auto-followup-check cron."""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "openclaw", "entrypoint.sh"
    )
    with open(path) as f:
        content = f.read()
    assert 'auto-followup-check' in content


def test_followup_tool_exists_in_server():
    """server.mjs should have get_stale_outreach tool."""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "openclaw", "mcp-badenleg-server", "server.mjs"
    )
    with open(path) as f:
        content = f.read()
    assert 'get_stale_outreach' in content
