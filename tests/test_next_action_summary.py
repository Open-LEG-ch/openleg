"""Tests for compute_next_action_summary and enriched /admin/strategy (US-005).

Covers:
- Next-action text for each demand level (high / medium / low / none)
- Formation-in-progress shortcut for high-demand municipalities
- Outreach-score influence on medium/low/none recommendations
- next_action and outreach_score present in /admin/strategy JSON response
- HTML template renders the next-action column
"""
import os
import json
import pytest
from unittest.mock import patch

from insights_engine import compute_next_action_summary


# ---------------------------------------------------------------------------
# Unit tests for compute_next_action_summary
# ---------------------------------------------------------------------------

class TestComputeNextActionSummary:

    def test_high_demand_no_formation_returns_contact_recommendation(self):
        """High demand without active formation → contact administrator."""
        result = compute_next_action_summary(
            demand_level="high",
            demand_score=60.0,
            outreach_score=70.0,
            verified_buildings=15,
            communities_in_formation=0,
        )
        assert isinstance(result, str)
        assert len(result) > 10
        # Should reference initiating LEG formation or contacting administrator
        assert any(kw in result.lower() for kw in ("formation", "contact", "administrator"))

    def test_high_demand_with_formation_returns_accelerate_recommendation(self):
        """High demand with formation in progress → accelerate onboarding."""
        result = compute_next_action_summary(
            demand_level="high",
            demand_score=65.0,
            outreach_score=75.0,
            verified_buildings=20,
            communities_in_formation=2,
        )
        assert isinstance(result, str)
        # Should reference existing formation activity
        assert any(kw in result.lower() for kw in ("formation", "already", "progress", "onboarding"))

    def test_medium_demand_high_outreach_returns_schedule_meeting(self):
        """Medium demand + high outreach score → schedule municipality meeting."""
        result = compute_next_action_summary(
            demand_level="medium",
            demand_score=25.0,
            outreach_score=60.0,
        )
        assert isinstance(result, str)
        assert any(kw in result.lower() for kw in ("meeting", "schedule", "engagement"))

    def test_medium_demand_low_outreach_returns_awareness_recommendation(self):
        """Medium demand + low outreach score → awareness content / workshop."""
        result = compute_next_action_summary(
            demand_level="medium",
            demand_score=18.0,
            outreach_score=30.0,
        )
        assert isinstance(result, str)
        assert any(kw in result.lower() for kw in ("awareness", "workshop", "pilot", "content"))

    def test_low_demand_high_outreach_returns_prospecting_recommendation(self):
        """Low demand + high outreach score → prospecting campaign."""
        result = compute_next_action_summary(
            demand_level="low",
            demand_score=8.0,
            outreach_score=65.0,
        )
        assert isinstance(result, str)
        assert any(kw in result.lower() for kw in ("campaign", "prospect", "transition", "registrant"))

    def test_low_demand_low_outreach_returns_monitor_recommendation(self):
        """Low demand + low outreach score → monitor / awareness updates."""
        result = compute_next_action_summary(
            demand_level="low",
            demand_score=5.0,
            outreach_score=20.0,
        )
        assert isinstance(result, str)
        assert any(kw in result.lower() for kw in ("monitor", "awareness", "sign-up", "registrant"))

    def test_none_demand_high_outreach_returns_awareness_campaign(self):
        """No demand signal + high outreach score → awareness campaign."""
        result = compute_next_action_summary(
            demand_level="none",
            demand_score=0.0,
            outreach_score=70.0,
        )
        assert isinstance(result, str)
        assert any(kw in result.lower() for kw in ("awareness", "campaign", "profile"))

    def test_none_demand_low_outreach_returns_watch_recommendation(self):
        """No demand signal + low outreach score → maintain watch."""
        result = compute_next_action_summary(
            demand_level="none",
            demand_score=0.0,
            outreach_score=10.0,
        )
        assert isinstance(result, str)
        assert any(kw in result.lower() for kw in ("watch", "no action", "revisit", "registration"))

    def test_all_levels_return_non_empty_string(self):
        """compute_next_action_summary returns non-empty string for all demand levels."""
        for level in ("high", "medium", "low", "none"):
            result = compute_next_action_summary(demand_level=level, demand_score=30.0)
            assert isinstance(result, str) and result.strip()


# ---------------------------------------------------------------------------
# Integration tests: /admin/strategy returns next_action and outreach_score
# ---------------------------------------------------------------------------

def _make_signal(bfs, name, demand_level, demand_score, signal_type="verified"):
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
        "heuristic_baseline": {"source": "public_data_only", "has_resident_data": True},
        "demand_level": demand_level,
        "signal_type": signal_type,
    }


MOCK_SIGNALS = [
    _make_signal(261, "Dietikon", "high", 55.0),
    _make_signal(230, "Winterthur", "medium", 25.0),
    _make_signal(242, "Urdorf", "low", 6.0),
    _make_signal(247, "Schlieren", "none", 0.0, "heuristic_only"),
]

MOCK_DEMAND_RESULT = {
    "signals": MOCK_SIGNALS,
    "computed_at": "2026-03-29T12:00:00",
    "bfs_number": None,
}

MOCK_PROFILES = [
    {"bfs_number": 261, "name": "Dietikon", "kanton": "ZH", "energy_transition_score": 70, "leg_value_gap_chf": 400},
    {"bfs_number": 230, "name": "Winterthur", "kanton": "ZH", "energy_transition_score": 55, "leg_value_gap_chf": 300},
    {"bfs_number": 242, "name": "Urdorf", "kanton": "ZH", "energy_transition_score": 40, "leg_value_gap_chf": 200},
    {"bfs_number": 247, "name": "Schlieren", "kanton": "ZH", "energy_transition_score": 30, "leg_value_gap_chf": 100},
]


@pytest.fixture
def strategy_app():
    """Minimal Flask app that wires the strategy view under test."""
    with patch('database.is_db_available', return_value=True), \
         patch('database.init_db', return_value=True), \
         patch('database.get_stats', return_value={'total_buildings': 0}), \
         patch('database.seed_default_tenant', return_value=True):

        from flask import Flask, request, jsonify, render_template, abort
        import database as db_mod

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
            from insights_engine import (
                compute_municipality_demand_signal,
                rank_municipalities_for_outreach,
                compute_next_action_summary,
            )
            data = compute_municipality_demand_signal()
            signals = data.get("signals", [])
            try:
                profiles = db_mod.get_all_municipality_profiles()
            except Exception:
                profiles = []
            ranked = rank_municipalities_for_outreach(profiles, data)
            outreach_by_bfs = {r["bfs_number"]: r for r in ranked}
            enriched = []
            for s in signals:
                bfs = s.get("bfs_number")
                outreach_entry = outreach_by_bfs.get(bfs, {})
                outreach_score = outreach_entry.get("outreach_score", 0.0)
                vd = s.get("verified_demand", {})
                next_action = compute_next_action_summary(
                    demand_level=s.get("demand_level", "none"),
                    demand_score=float(vd.get("demand_score") or 0),
                    outreach_score=float(outreach_score),
                    verified_buildings=int(vd.get("verified_buildings") or 0),
                    communities_in_formation=int(vd.get("communities_in_formation") or 0),
                )
                enriched.append({**s, "outreach_score": outreach_score, "next_action": next_action})
            level_order = {"high": 0, "medium": 1, "low": 2, "none": 3}
            signals_sorted = sorted(
                enriched,
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


class TestAdminStrategyNextAction:

    def test_json_includes_next_action_field(self, client):
        """Each signal in the JSON response contains a 'next_action' string."""
        with patch('insights_engine.compute_municipality_demand_signal',
                   return_value=MOCK_DEMAND_RESULT), \
             patch('database.get_all_municipality_profiles',
                   return_value=MOCK_PROFILES):
            resp = client.get('/admin/strategy?token=test-admin-token')
        assert resp.status_code == 200
        data = json.loads(resp.data)
        for s in data["signals"]:
            assert "next_action" in s, f"Missing next_action for {s.get('name')}"
            assert isinstance(s["next_action"], str) and s["next_action"].strip()

    def test_json_includes_outreach_score_field(self, client):
        """Each signal in the JSON response contains a numeric 'outreach_score'."""
        with patch('insights_engine.compute_municipality_demand_signal',
                   return_value=MOCK_DEMAND_RESULT), \
             patch('database.get_all_municipality_profiles',
                   return_value=MOCK_PROFILES):
            resp = client.get('/admin/strategy?token=test-admin-token')
        data = json.loads(resp.data)
        for s in data["signals"]:
            assert "outreach_score" in s
            assert isinstance(s["outreach_score"], (int, float))

    def test_high_demand_next_action_references_formation_or_contact(self, client):
        """High-demand municipality next_action text references LEG formation or contact."""
        with patch('insights_engine.compute_municipality_demand_signal',
                   return_value=MOCK_DEMAND_RESULT), \
             patch('database.get_all_municipality_profiles',
                   return_value=MOCK_PROFILES):
            resp = client.get('/admin/strategy?token=test-admin-token')
        data = json.loads(resp.data)
        high_entries = [s for s in data["signals"] if s["demand_level"] == "high"]
        assert high_entries, "Expected at least one high-demand entry"
        for entry in high_entries:
            text = entry["next_action"].lower()
            assert any(kw in text for kw in ("formation", "contact", "administrator", "onboarding"))

    def test_html_renders_next_action_column(self, client):
        """HTML response includes the Next Action column header and recommendation text."""
        with patch('insights_engine.compute_municipality_demand_signal',
                   return_value=MOCK_DEMAND_RESULT), \
             patch('database.get_all_municipality_profiles',
                   return_value=MOCK_PROFILES):
            resp = client.get(
                '/admin/strategy?token=test-admin-token',
                headers={'Accept': 'text/html'}
            )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'Next Action' in html
        # At least one municipality-specific recommendation should appear
        assert any(phrase in html for phrase in ("formation", "Formation", "campaign", "Campaign",
                                                  "awareness", "Awareness", "monitor", "Monitor",
                                                  "contact", "Contact", "meeting", "Meeting"))

    def test_no_profiles_fallback_still_includes_next_action(self, client):
        """When profile DB is unavailable, next_action is still populated from demand alone."""
        with patch('insights_engine.compute_municipality_demand_signal',
                   return_value=MOCK_DEMAND_RESULT), \
             patch('database.get_all_municipality_profiles', side_effect=Exception("DB error")):
            resp = client.get('/admin/strategy?token=test-admin-token')
        assert resp.status_code == 200
        data = json.loads(resp.data)
        for s in data["signals"]:
            assert "next_action" in s
            assert s["next_action"].strip()
