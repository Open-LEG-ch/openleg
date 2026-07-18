# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the LEG operator dashboard (/leg/dashboard).

The dashboard surfaces formation_wizard.get_community_status (readiness
score, members, next steps) to community members via the same
capability-URL pattern as the resident dashboard (?cid=...&bid=...).
Non-members get the error view, never another community's data.
"""

import os
from unittest.mock import MagicMock

import dashboard as dashboard_module

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STATUS = {
    "community_id": "c0ffee",
    "name": "LEG Musterweg",
    "status": "interested",
    "distribution_model": "simple",
    "admin_building_id": "b-admin",
    "created_at": None,
    "formation_started_at": None,
    "dso_submitted_at": None,
    "member_count": {"total": 2, "confirmed": 1, "invited": 1},
    "readiness_score": 0,
    "members": [
        {"building_id": "b-admin", "role": "admin", "status": "confirmed"},
        {"building_id": "b-guest", "role": "member", "status": "invited"},
    ],
    "documents": None,
    "next_steps": ["Invite at least 2 more neighbors"],
}


def test_leg_overview_returns_status_for_member(monkeypatch):
    monkeypatch.setattr(
        dashboard_module.formation_wizard,
        "get_community_status",
        MagicMock(return_value=dict(STATUS)),
    )
    result = dashboard_module.leg_overview("c0ffee", "b-admin")
    assert result["error"] is None
    assert result["community"]["name"] == "LEG Musterweg"
    assert result["is_admin"] is True
    assert result["viewer_building_id"] == "b-admin"


def test_leg_overview_member_is_not_admin(monkeypatch):
    monkeypatch.setattr(
        dashboard_module.formation_wizard,
        "get_community_status",
        MagicMock(return_value=dict(STATUS)),
    )
    result = dashboard_module.leg_overview("c0ffee", "b-guest")
    assert result["error"] is None
    assert result["is_admin"] is False


def test_leg_overview_rejects_non_member(monkeypatch):
    monkeypatch.setattr(
        dashboard_module.formation_wizard,
        "get_community_status",
        MagicMock(return_value=dict(STATUS)),
    )
    result = dashboard_module.leg_overview("c0ffee", "b-stranger")
    assert result["error"]
    assert result["community"] is None


def test_leg_overview_missing_params(monkeypatch):
    called = MagicMock()
    monkeypatch.setattr(
        dashboard_module.formation_wizard, "get_community_status", called
    )
    assert dashboard_module.leg_overview("", "b-admin")["error"]
    assert dashboard_module.leg_overview("c0ffee", "")["error"]
    called.assert_not_called()


def test_leg_overview_unknown_community(monkeypatch):
    monkeypatch.setattr(
        dashboard_module.formation_wizard,
        "get_community_status",
        MagicMock(return_value=None),
    )
    result = dashboard_module.leg_overview("nope", "b-admin")
    assert result["error"]
    assert result["community"] is None


def test_leg_demo_overview_is_selfcontained():
    result = dashboard_module.leg_demo_overview()
    assert result["error"] is None
    assert result["community"]["readiness_score"] > 0
    assert result["community"]["next_steps"]


def test_leg_dashboard_routes_in_source():
    with open(os.path.join(PROJECT_ROOT, "app.py"), encoding="utf-8") as handle:
        source = handle.read()
    assert '"/leg/dashboard"' in source
    assert '"/leg/dashboard/demo"' in source


def test_leg_dashboard_template_contract():
    path = os.path.join(PROJECT_ROOT, "templates", "leg_dashboard.html")
    with open(path, encoding="utf-8") as handle:
        html = handle.read()
    assert '{% extends "base.html" %}' in html
    assert "cdn.tailwindcss.com" not in html
    assert "readiness_score" in html
    assert "next_steps" in html
