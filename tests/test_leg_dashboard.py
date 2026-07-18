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


# --- Formation actions (Slice 3) ---


def _patch_status(monkeypatch, status=None):
    monkeypatch.setattr(
        dashboard_module.formation_wizard,
        "get_community_status",
        MagicMock(return_value=dict(STATUS) if status is None else status),
    )


def test_leg_create_calls_formation_wizard(monkeypatch):
    created = {"community_id": "new-cid", "name": "LEG Neu"}
    mock_create = MagicMock(return_value=created)
    monkeypatch.setattr(
        dashboard_module.formation_wizard, "create_community", mock_create
    )
    result = dashboard_module.leg_create("LEG Neu", "b-admin", "simple")
    assert result["error"] is None
    assert result["community_id"] == "new-cid"
    args, _ = mock_create.call_args
    assert args[1] == "LEG Neu"
    assert args[2] == "b-admin"


def test_leg_create_requires_name_and_bid(monkeypatch):
    mock_create = MagicMock()
    monkeypatch.setattr(
        dashboard_module.formation_wizard, "create_community", mock_create
    )
    assert dashboard_module.leg_create("", "b-admin", "simple")["error"]
    assert dashboard_module.leg_create("LEG Neu", "", "simple")["error"]
    mock_create.assert_not_called()


def test_leg_invite_requires_admin(monkeypatch):
    _patch_status(monkeypatch)
    mock_invite = MagicMock(return_value=True)
    monkeypatch.setattr(dashboard_module.formation_wizard, "invite_member", mock_invite)

    result = dashboard_module.leg_invite("c0ffee", "b-guest", "b-new")
    assert result["error"]
    mock_invite.assert_not_called()


def test_leg_invite_as_admin_calls_wizard(monkeypatch):
    _patch_status(monkeypatch)
    mock_invite = MagicMock(return_value=True)
    monkeypatch.setattr(dashboard_module.formation_wizard, "invite_member", mock_invite)

    result = dashboard_module.leg_invite("c0ffee", "b-admin", "b-new")
    assert result["error"] is None
    args, _ = mock_invite.call_args
    assert args[1] == "c0ffee"
    assert args[2] == "b-new"
    assert args[3] == "b-admin"


def test_leg_confirm_confirms_own_membership(monkeypatch):
    mock_confirm = MagicMock(return_value=True)
    monkeypatch.setattr(
        dashboard_module.formation_wizard, "confirm_membership", mock_confirm
    )
    result = dashboard_module.leg_confirm("c0ffee", "b-guest")
    assert result["error"] is None
    args, _ = mock_confirm.call_args
    assert args[1] == "c0ffee"
    assert args[2] == "b-guest"


def test_leg_start_formation_requires_admin(monkeypatch):
    _patch_status(monkeypatch)
    mock_start = MagicMock(return_value=True)
    monkeypatch.setattr(
        dashboard_module.formation_wizard, "start_formation", mock_start
    )
    result = dashboard_module.leg_start_formation("c0ffee", "b-guest")
    assert result["error"]
    mock_start.assert_not_called()


def test_leg_start_formation_as_admin(monkeypatch):
    _patch_status(monkeypatch)
    mock_start = MagicMock(return_value=True)
    monkeypatch.setattr(
        dashboard_module.formation_wizard, "start_formation", mock_start
    )
    result = dashboard_module.leg_start_formation("c0ffee", "b-admin")
    assert result["error"] is None
    mock_start.assert_called_once()


def test_formation_action_routes_in_source():
    with open(os.path.join(PROJECT_ROOT, "app.py"), encoding="utf-8") as handle:
        source = handle.read()
    assert '"/leg/community/create"' in source
    assert '"/leg/community/<community_id>/invite"' in source
    assert '"/leg/community/<community_id>/confirm"' in source
    assert '"/leg/community/<community_id>/start-formation"' in source


def test_leg_dashboard_template_has_action_forms():
    path = os.path.join(PROJECT_ROOT, "templates", "leg_dashboard.html")
    with open(path, encoding="utf-8") as handle:
        html = handle.read()
    assert "/invite" in html
    assert "/start-formation" in html
    assert "/confirm" in html
