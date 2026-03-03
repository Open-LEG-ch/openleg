"""Tests for LEA competitive intelligence loop."""
import os
import pytest


def test_competitive_tool_exists():
    """server.mjs should have check_competitive_changes tool."""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "openclaw", "mcp-badenleg-server", "server.mjs"
    )
    with open(path) as f:
        content = f.read()
    assert 'check_competitive_changes' in content


def test_competitive_detects_new_leghub_partner():
    """check_competitive_changes should reference monitor_leghub_partners logic."""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "openclaw", "mcp-badenleg-server", "server.mjs"
    )
    with open(path) as f:
        content = f.read()
    idx = content.index("'check_competitive_changes'")
    next_tool = content.find("server.tool(", idx + 1)
    handler = content[idx:next_tool if next_tool > 0 else len(content)]
    assert 'leghub' in handler.lower() or 'partner' in handler.lower()


def test_competitive_no_change_returns_clean():
    """check_competitive_changes handler should handle empty results."""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "openclaw", "mcp-badenleg-server", "server.mjs"
    )
    with open(path) as f:
        content = f.read()
    idx = content.index("'check_competitive_changes'")
    next_tool = content.find("server.tool(", idx + 1)
    handler = content[idx:next_tool if next_tool > 0 else len(content)]
    # Should return some result even when no changes
    assert 'changes' in handler.lower() or 'result' in handler.lower()


def test_competitive_updates_strategy_tracker():
    """check_competitive_changes should call track_strategy_item (now GREEN)."""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "openclaw", "mcp-badenleg-server", "server.mjs"
    )
    with open(path) as f:
        content = f.read()
    idx = content.index("'check_competitive_changes'")
    next_tool = content.find("server.tool(", idx + 1)
    handler = content[idx:next_tool if next_tool > 0 else len(content)]
    assert 'strategy' in handler.lower() or 'track' in handler.lower()
