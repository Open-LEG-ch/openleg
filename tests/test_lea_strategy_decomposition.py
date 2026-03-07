"""Tests for LEA strategy decomposition."""

import os
from unittest.mock import MagicMock, patch


def test_strategy_subtasks_table_exists():
    """database.py should define strategy_subtasks table."""
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database.py')
    with open(db_path) as f:
        content = f.read()
    assert 'strategy_subtasks' in content
    assert 'parent_week' in content
    assert 'parent_item' in content


def test_create_subtask():
    """create_strategy_subtask should insert a subtask."""
    import database as db_mod

    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = {'id': 1}
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = lambda s: mock_cur
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    with patch('database.get_connection') as mock_gc:
        mock_gc.return_value.__enter__ = lambda s: mock_conn
        mock_gc.return_value.__exit__ = MagicMock(return_value=False)
        result = db_mod.create_strategy_subtask(1, 'seed-municipalities', 'Research top 10 cantons')
        assert result is True


def test_get_subtasks_for_item():
    """get_strategy_subtasks should return subtasks for a parent item."""
    import database as db_mod

    mock_cur = MagicMock()
    mock_cur.fetchall.return_value = [
        {'id': 1, 'subtask': 'Research', 'status': 'pending'},
        {'id': 2, 'subtask': 'Draft email', 'status': 'done'},
    ]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = lambda s: mock_cur
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    with patch('database.get_connection') as mock_gc:
        mock_gc.return_value.__enter__ = lambda s: mock_conn
        mock_gc.return_value.__exit__ = MagicMock(return_value=False)
        results = db_mod.get_strategy_subtasks(1, 'seed-municipalities')
        assert len(results) == 2


def test_complete_subtask_updates_parent():
    """update_strategy_subtask should update status."""
    import database as db_mod

    mock_cur = MagicMock()
    mock_cur.rowcount = 1
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = lambda s: mock_cur
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    with patch('database.get_connection') as mock_gc:
        mock_gc.return_value.__enter__ = lambda s: mock_conn
        mock_gc.return_value.__exit__ = MagicMock(return_value=False)
        result = db_mod.update_strategy_subtask(1, 'done', 'Completed research')
        assert result is True


def test_decompose_tool_exists():
    """server.mjs should have decompose_strategy_item tool."""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'openclaw', 'mcp-openleg-server', 'server.mjs'
    )
    with open(path) as f:
        content = f.read()
    assert 'decompose_strategy_item' in content
    assert 'get_strategy_subtasks' in content
    assert 'update_strategy_subtask' in content


def test_subtask_fail_closed():
    """On DB error, create_strategy_subtask should return False."""
    import database as db_mod

    with patch('database.get_connection', side_effect=Exception('DB down')):
        result = db_mod.create_strategy_subtask(1, 'test', 'test')
        assert result is False
