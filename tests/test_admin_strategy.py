# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the /admin/strategy route (US-002).

Covers:
- JSON response contract for demand-aware operator view
- HTML rendering path with sorted demand signals
- Demand level ordering (high → medium → low → none)
- Authentication guard
"""
import os
import json
import pytest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_demand_signal(bfs, name, demand_level, demand_score, signal_type="verified"):
    return {
        "bfs_number": bfs,
        "name": name,
        "kanton": "ZH",
        "verified_demand": {
            "verified_buildings": 5,
            "recent_signups_90d": 2,
            "confirmed_leg_members": 1,
            "communities_in_formation": 0,
            "meter_data_uploads": 0,
            "demand_score": demand_score,
        },
        "heuristic_baseline": {
            "source": "public_data_only",
            "has_resident_data": signal_type == "verified",
        },
        "demand_level": demand_level,
        "signal_type": signal_type,
    }


MOCK_SIGNALS_MIXED = [
    _make_demand_signal(261, "Dietikon", "high", 55.0),
    _make_demand_signal(247, "Schlieren", "none", 0.0, "heuristic_only"),
    _make_demand_signal(230, "Winterthur", "medium", 25.0),
    _make_demand_signal(242, "Urdorf", "low", 6.0),
]

MOCK_DEMAND_RESULT = {
    "signals": MOCK_SIGNALS_MIXED,
    "computed_at": "2026-03-29T12:00:00",
    "bfs_number": None,
}


# ---------------------------------------------------------------------------
# Fixture: lightweight Flask test app with /admin/strategy only
# ---------------------------------------------------------------------------

@pytest.fixture
def strategy_app():
    """Minimal Flask app that wires the strategy view under test."""
    with patch('database.is_db_available', return_value=True), \
         patch('database.init_db', return_value=True), \
         patch('database.get_stats', return_value={'total_buildings': 0}), \
         patch('database.seed_default_tenant', return_value=True):

        from flask import Flask, request, jsonify, render_template, abort
        import sys

        test_app = Flask(
            __name__,
            template_folder=os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                'templates'
            )
        )
        test_app.config['TESTING'] = True
        ADMIN_TOKEN = 'test-admin-token'

        def _require_admin():
            token = request.headers.get('X-Admin-Token') or request.args.get('token') or ''
            if token != ADMIN_TOKEN:
                abort(403)

        @test_app.route('/admin/strategy')
        def admin_strategy():
            _require_admin()
            from insights_engine import compute_municipality_demand_signal
            data = compute_municipality_demand_signal()
            signals = data.get("signals", [])
            level_order = {"high": 0, "medium": 1, "low": 2, "none": 3}
            signals_sorted = sorted(
                signals,
                key=lambda s: (level_order.get(s.get("demand_level", "none"), 3),
                               -s.get("verified_demand", {}).get("demand_score", 0))
            )
            if 'text/html' in (request.headers.get('Accept') or ''):
                return render_template('admin/strategy.html',
                                       signals=signals_sorted,
                                       computed_at=data.get("computed_at"))
            return jsonify({"signals": signals_sorted, "computed_at": data.get("computed_at")})

        yield test_app


@pytest.fixture
def client(strategy_app):
    return strategy_app.test_client()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAdminStrategyRoute:

    def test_json_response_contract(self, client):
        """GET /admin/strategy returns valid JSON with 'signals' and 'computed_at'."""
        with patch('insights_engine.compute_municipality_demand_signal',
                   return_value=MOCK_DEMAND_RESULT):
            resp = client.get(
                '/admin/strategy?token=test-admin-token',
                headers={'Accept': 'application/json'}
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "signals" in data
        assert "computed_at" in data

    def test_signals_sorted_high_first(self, client):
        """High-demand municipalities appear before medium/low/none in JSON response."""
        with patch('insights_engine.compute_municipality_demand_signal',
                   return_value=MOCK_DEMAND_RESULT):
            resp = client.get('/admin/strategy?token=test-admin-token')
        data = json.loads(resp.data)
        levels = [s["demand_level"] for s in data["signals"]]
        assert levels[0] == "high", "First entry should be highest demand"
        assert levels[-1] == "none", "Last entry should have no demand signal"

    def test_html_rendering(self, client):
        """GET /admin/strategy with Accept: text/html returns a rendered HTML page."""
        with patch('insights_engine.compute_municipality_demand_signal',
                   return_value=MOCK_DEMAND_RESULT):
            resp = client.get(
                '/admin/strategy?token=test-admin-token',
                headers={'Accept': 'text/html'}
            )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'Municipality Demand Strategy' in html
        assert 'Dietikon' in html
        assert 'Winterthur' in html

    def test_html_highlights_actionable(self, client):
        """HTML view marks high/medium demand municipalities with action prompt."""
        with patch('insights_engine.compute_municipality_demand_signal',
                   return_value=MOCK_DEMAND_RESULT):
            resp = client.get(
                '/admin/strategy?token=test-admin-token',
                headers={'Accept': 'text/html'}
            )
        html = resp.data.decode()
        assert 'Act now' in html

    def test_html_shows_demand_level_badges(self, client):
        """HTML view renders demand level labels for all municipalities."""
        with patch('insights_engine.compute_municipality_demand_signal',
                   return_value=MOCK_DEMAND_RESULT):
            resp = client.get(
                '/admin/strategy?token=test-admin-token',
                headers={'Accept': 'text/html'}
            )
        html = resp.data.decode()
        for level in ('high', 'medium', 'low', 'none'):
            assert level in html

    def test_missing_token_returns_403(self, client):
        """Requests without a valid admin token are rejected with 403."""
        resp = client.get('/admin/strategy')
        assert resp.status_code == 403

    def test_empty_signals(self, client):
        """When no municipality data exists, endpoint still returns valid structure."""
        empty_result = {"signals": [], "computed_at": "2026-03-29T12:00:00", "bfs_number": None}
        with patch('insights_engine.compute_municipality_demand_signal',
                   return_value=empty_result):
            resp = client.get('/admin/strategy?token=test-admin-token')
        data = json.loads(resp.data)
        assert data["signals"] == []

    def test_demand_score_present_in_signals(self, client):
        """Each signal in the JSON response includes a numeric demand_score."""
        with patch('insights_engine.compute_municipality_demand_signal',
                   return_value=MOCK_DEMAND_RESULT):
            resp = client.get('/admin/strategy?token=test-admin-token')
        data = json.loads(resp.data)
        for s in data["signals"]:
            assert "demand_score" in s["verified_demand"]
            assert isinstance(s["verified_demand"]["demand_score"], (int, float))
