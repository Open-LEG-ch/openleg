"""Tests for LEA community health monitoring."""

import os
from unittest.mock import MagicMock, patch


def test_health_flags_member_drop():
    """get_community_health_issues should flag communities with member drops."""
    import database as db_mod

    mock_cur = MagicMock()
    mock_cur.fetchall.return_value = [
        {'community_id': 'com-1', 'issue': 'member_drop', 'detail': '2 members left in 7 days'}
    ]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = lambda s: mock_cur
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    with patch('database.get_connection') as mock_gc:
        mock_gc.return_value.__enter__ = lambda s: mock_conn
        mock_gc.return_value.__exit__ = MagicMock(return_value=False)
        results = db_mod.get_community_health_issues()
        assert len(results) >= 1
        assert results[0]['issue'] == 'member_drop'


def test_health_flags_stale_meter_data():
    """get_community_health_issues should flag communities with stale meter data."""
    import database as db_mod

    mock_cur = MagicMock()
    mock_cur.fetchall.side_effect = [
        [],
        [{'community_id': 'com-2', 'issue': 'stale_meter_data', 'detail': 'No data for 30 days'}],
    ]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = lambda s: mock_cur
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    with patch('database.get_connection') as mock_gc:
        mock_gc.return_value.__enter__ = lambda s: mock_conn
        mock_gc.return_value.__exit__ = MagicMock(return_value=False)
        results = db_mod.get_community_health_issues()
        assert any(r.get('issue') == 'stale_meter_data' for r in results)


def test_health_clean_community_not_flagged():
    """Healthy community should return empty list."""
    import database as db_mod

    mock_cur = MagicMock()
    mock_cur.fetchall.return_value = []
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = lambda s: mock_cur
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    with patch('database.get_connection') as mock_gc:
        mock_gc.return_value.__enter__ = lambda s: mock_conn
        mock_gc.return_value.__exit__ = MagicMock(return_value=False)
        results = db_mod.get_community_health_issues()
        assert results == []


def test_health_tool_exists_in_server():
    """server.mjs should have get_community_health tool."""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'openclaw', 'mcp-openleg-server', 'server.mjs'
    )
    with open(path) as f:
        content = f.read()
    assert 'get_community_health' in content


def test_health_fail_closed_on_db_error():
    """On DB error, get_community_health_issues should return empty (fail-closed)."""
    import database as db_mod

    with patch('database.get_connection', side_effect=Exception('DB down')):
        results = db_mod.get_community_health_issues()
        assert results == []
