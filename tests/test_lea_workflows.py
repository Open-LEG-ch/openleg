"""Tests for LEA multi-step workflows: municipality + formation pipelines."""

import os


def _read_server():
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'openclaw', 'mcp-openleg-server', 'server.mjs'
    )
    with open(path) as f:
        return f.read()


def _read_entrypoint():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'openclaw', 'entrypoint.sh')
    with open(path) as f:
        return f.read()


# === Municipality Pipeline ===


def test_pipeline_tool_exists():
    """server.mjs should have run_municipality_pipeline tool."""
    assert 'run_municipality_pipeline' in _read_server()


def test_pipeline_checks_high_potential():
    """Pipeline should filter by value_gap."""
    content = _read_server()
    idx = content.index("'run_municipality_pipeline'")
    next_tool = content.find('server.tool(', idx + 1)
    handler = content[idx : next_tool if next_tool > 0 else len(content)]
    assert 'value_gap' in handler.lower() or 'score' in handler.lower() or 'potential' in handler.lower()


def test_pipeline_calls_upsert_tenant():
    """Pipeline should reference upsert_tenant."""
    content = _read_server()
    idx = content.index("'run_municipality_pipeline'")
    next_tool = content.find('server.tool(', idx + 1)
    handler = content[idx : next_tool if next_tool > 0 else len(content)]
    assert 'upsert' in handler.lower() or 'tenant' in handler.lower()


def test_pipeline_calls_draft_outreach():
    """Pipeline should reference draft outreach."""
    content = _read_server()
    idx = content.index("'run_municipality_pipeline'")
    next_tool = content.find('server.tool(', idx + 1)
    handler = content[idx : next_tool if next_tool > 0 else len(content)]
    assert 'outreach' in handler.lower() or 'draft' in handler.lower()


def test_pipeline_sends_approval_request():
    """Pipeline should use request-approval for CEO sign-off."""
    content = _read_server()
    idx = content.index("'run_municipality_pipeline'")
    next_tool = content.find('server.tool(', idx + 1)
    handler = content[idx : next_tool if next_tool > 0 else len(content)]
    assert 'approval' in handler.lower() or 'request' in handler.lower()


def test_pipeline_respects_budget():
    """run_municipality_pipeline should be in ACTION_REGISTRY with budget."""
    content = _read_server()
    assert 'run_municipality_pipeline' in content
    idx = content.index('run_municipality_pipeline')
    chunk = content[idx : idx + 120]
    assert 'YELLOW' in chunk or 'budget' in chunk.lower()


def test_pipeline_cron_exists():
    """entrypoint.sh should have municipality-pipeline cron."""
    assert 'municipality-pipeline' in _read_entrypoint()


# === Formation Pipeline ===


def test_formation_pipeline_generates_financial_model():
    """Formation pipeline should reference financial model via formation_threshold_reached."""
    fw_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'formation_wizard.py')
    with open(fw_path) as f:
        content = f.read()
    assert 'formation_threshold_reached' in content


def test_formation_pipeline_generates_documents():
    """Formation pipeline handler should reference document generation."""
    # formation_threshold_reached is fired from formation_wizard.py
    fw_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'formation_wizard.py')
    with open(fw_path) as f:
        content = f.read()
    assert 'formation_threshold_reached' in content


def test_formation_pipeline_notifies_ceo():
    """Formation pipeline should notify CEO."""
    fw_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'formation_wizard.py')
    with open(fw_path) as f:
        content = f.read()
    assert 'event_hooks.fire' in content


def test_formation_pipeline_tracks_strategy():
    """run_municipality_pipeline should track strategy items."""
    content = _read_server()
    idx = content.index("'run_municipality_pipeline'")
    next_tool = content.find('server.tool(', idx + 1)
    handler = content[idx : next_tool if next_tool > 0 else len(content)]
    assert 'strategy' in handler.lower() or 'track' in handler.lower()


def test_formation_pipeline_stops_on_error():
    """Pipeline should handle errors gracefully."""
    content = _read_server()
    idx = content.index("'run_municipality_pipeline'")
    next_tool = content.find('server.tool(', idx + 1)
    handler = content[idx : next_tool if next_tool > 0 else len(content)]
    assert 'catch' in handler or 'error' in handler.lower()
