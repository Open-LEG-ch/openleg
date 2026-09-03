# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the session-gated LEG operator dashboard (/leg/dashboard)."""

import os
from unittest.mock import MagicMock

import pytest

import dashboard as dashboard_module
from formation_wizard import _get_next_steps
from tests.test_dashboard_access_routes import app_module  # noqa: F401

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
    "next_steps": ["Laden Sie mindestens 2 weitere Nachbarn ein."],
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
    with open(
        os.path.join(PROJECT_ROOT, "dashboard_routes.py"), encoding="utf-8"
    ) as handle:
        source = handle.read()
    assert '"/leg/dashboard"' in source
    assert '"/leg/dashboard/demo"' in source


def test_leg_dashboard_template_contract():
    path = os.path.join(PROJECT_ROOT, "templates", "leg_dashboard.html")
    with open(path, encoding="utf-8") as handle:
        html = handle.read()
    assert '{% extends "product_base.html" %}' in html
    assert "cdn.tailwindcss.com" not in html
    assert "readiness_score" in html
    assert "next_steps" in html


def test_member_table_fits_mobile():
    path = os.path.join(PROJECT_ROOT, "templates", "leg_dashboard.html")
    with open(path, encoding="utf-8") as handle:
        html = handle.read()
    start = html.index("Mitglieder</h2>")
    members_table = html[start : html.index("</section>", start)]
    assert "min-w-[32rem]" not in members_table
    assert "table-fixed" in members_table
    assert "sm:table-auto" in members_table
    for segment in members_table.split("<td")[1:]:
        assert "break-words" in segment.split(">", 1)[0]


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
    assert args[0] == "LEG Neu"
    assert args[1] == "b-admin"


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
    assert args[0] == "c0ffee"
    assert args[1] == "b-new"
    assert args[2] == "b-admin"


def test_leg_confirm_confirms_own_membership(monkeypatch):
    mock_confirm = MagicMock(return_value=True)
    monkeypatch.setattr(
        dashboard_module.formation_wizard, "confirm_membership", mock_confirm
    )
    result = dashboard_module.leg_confirm("c0ffee", "b-guest")
    assert result["error"] is None
    args, _ = mock_confirm.call_args
    assert args[0] == "c0ffee"
    assert args[1] == "b-guest"


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


def test_leg_dashboard_location_encodes_untrusted_values():
    # CodeQL: a user-provided cid must not steer the redirect off the route.
    location = dashboard_module.leg_dashboard_location("//evil.com#fragment")
    assert location.startswith("/leg/dashboard?")
    assert "//evil.com" not in location
    assert "#" not in location
    assert "bid=" not in location


def test_formation_action_routes_in_source():
    with open(
        os.path.join(PROJECT_ROOT, "dashboard_routes.py"), encoding="utf-8"
    ) as handle:
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


# --- German status vocabulary (Slice 1) ---

_ENGLISH_MARKERS = [
    "Collect",
    "Submit",
    "Generate",
    "Invite",
    "Wait for",
    "Configure",
    "Review",
]

_RAW_MACHINE_TOKENS = [
    "documents_generated",
    "signatures_pending",
    "dso_submitted",
    "dso_approved",
    "formation_started",
    "proportional",
    "simple",
    "custom",
    "invited",
    "member",
]

_EXPECTED_STATUS_LABELS = {
    "interested": "Interessiert",
    "invited": "Eingeladen",
    "confirmed": "Bestätigt",
    "formation_started": "Gründung gestartet",
    "documents_generated": "Dokumente erstellt",
    "signatures_pending": "Unterschriften ausstehend",
    "dso_submitted": "Netzbetreiber informiert",
    "dso_approved": "Netzbetreiber hat bewilligt",
    "active": "Aktiv",
    "rejected": "Abgelehnt",
}

_GET_NEXT_STEP_CASES = [
    ("interested", 1, ["Laden Sie mindestens 2 weitere Nachbarn ein."]),
    ("interested", 3, ["Starten Sie den Gründungsprozess."]),
    (
        "formation_started",
        3,
        [
            "Erstellen Sie die rechtlichen Dokumente.",
            "Prüfen Sie die Gemeinschaftsvereinbarung.",
        ],
    ),
    (
        "documents_generated",
        3,
        [
            "Sammeln Sie die Unterschriften aller Mitglieder.",
            "Prüfen Sie die Teilnehmerverträge.",
        ],
    ),
    ("signatures_pending", 3, ["Melden Sie die LEG beim Netzbetreiber an."]),
    (
        "dso_submitted",
        3,
        ["Warten Sie auf die Bewilligung durch den Netzbetreiber (bis zu 30 Tage)."],
    ),
    (
        "dso_approved",
        3,
        [
            "Legen Sie das Aktivierungsdatum fest.",
            "Richten Sie die Abrechnung ein.",
        ],
    ),
]


def _demo_community(status, distribution_model="proportional", confirmed_count=1):
    community = {
        "community_id": "demo-leg",
        "name": "LEG Musterweg",
        "status": status,
        "distribution_model": distribution_model,
        "member_count": {
            "total": confirmed_count + 1,
            "confirmed": confirmed_count,
            "invited": 1,
        },
        "readiness_score": 0,
        "members": [
            {
                "building_id": "demo-building",
                "role": "admin",
                "status": "confirmed",
                "address": "Musterweg 1",
            },
            {
                "building_id": "demo-2",
                "role": "member",
                "status": "invited",
                "address": "Musterweg 3",
            },
        ],
        "documents": None,
        "next_steps": _get_next_steps(status, confirmed_count),
    }
    return dashboard_module._with_german_labels(community)


def _render_demo(app_module, monkeypatch, community):  # noqa: F811
    monkeypatch.setattr(
        dashboard_module,
        "leg_demo_overview",
        lambda: {
            "error": None,
            "viewer_building_id": "demo-building",
            "is_admin": True,
            "community": community,
        },
    )
    client = app_module.web.test_client()
    return client.get("/leg/dashboard/demo").get_data(as_text=True)


@pytest.mark.parametrize("status,confirmed_count,expected", _GET_NEXT_STEP_CASES)
def test_get_next_steps_returns_german(status, confirmed_count, expected):
    steps = _get_next_steps(status, confirmed_count)
    assert steps == expected
    joined = " ".join(steps)
    for marker in _ENGLISH_MARKERS:
        assert marker not in joined


def test_get_next_steps_returns_empty_for_inactive_statuses():
    for status in ("invited", "confirmed", "active", "rejected"):
        assert _get_next_steps(status, 3) == []


def test_get_next_steps_unknown_status_degrades_safely():
    assert _get_next_steps("not_a_status", 3) == []


@pytest.mark.parametrize("status", list(_EXPECTED_STATUS_LABELS))
def test_leg_dashboard_renders_german_status_label(app_module, monkeypatch, status):  # noqa: F811
    community = _demo_community(status)
    html = _render_demo(app_module, monkeypatch, community)
    assert _EXPECTED_STATUS_LABELS[status] in html
    assert status not in html
    for marker in _ENGLISH_MARKERS:
        assert marker not in html


def test_leg_dashboard_renders_no_machine_tokens(app_module, monkeypatch):  # noqa: F811
    community = _demo_community(
        "documents_generated", distribution_model="proportional", confirmed_count=2
    )
    html = _render_demo(app_module, monkeypatch, community)
    for token in _RAW_MACHINE_TOKENS:
        assert token not in html
    for marker in _ENGLISH_MARKERS:
        assert marker not in html
    assert "Dokumente erstellt" in html
    assert "Nach Verbrauch und Erzeugung" in html
    assert "Mitglied" in html
    assert "Eingeladen" in html
    assert "Sammeln Sie die Unterschriften aller Mitglieder." in html


def test_leg_dashboard_renders_fallback_for_unknown_status(app_module, monkeypatch):  # noqa: F811
    community = _demo_community("not_a_status")
    html = _render_demo(app_module, monkeypatch, community)
    assert "Status wird geprüft" in html
    assert "not_a_status" not in html
