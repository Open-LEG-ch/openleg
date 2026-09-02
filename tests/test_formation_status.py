# SPDX-License-Identifier: AGPL-3.0-or-later
"""Direct contracts for the community formation status read model."""

from unittest.mock import patch

import pytest

from formation_wizard import FormationStatus, get_community_status


def _row(status, members):
    return {
        "community_id": "community-1",
        "name": "LEG Musterweg",
        "status": status,
        "distribution_model": "proportional",
        "admin_building_id": "building-admin",
        "created_at": None,
        "formation_started_at": None,
        "dso_submitted_at": None,
        "members": members,
    }


def _confirmed_members(count=3):
    return [
        {"building_id": f"building-{number}", "status": "confirmed"}
        for number in range(count)
    ]


@pytest.mark.parametrize(
    ("status", "expected_score"),
    [
        (FormationStatus.FORMATION_STARTED.value, 30),
        (FormationStatus.DOCUMENTS_GENERATED.value, 60),
        (FormationStatus.SIGNATURES_PENDING.value, 60),
        (FormationStatus.DSO_SUBMITTED.value, 80),
        (FormationStatus.DSO_APPROVED.value, 100),
        (FormationStatus.ACTIVE.value, 100),
        (FormationStatus.REJECTED.value, 0),
    ],
)
def test_community_readiness_follows_the_weighted_state_table(status, expected_score):
    with patch(
        "database.fetch_community_with_members",
        return_value=_row(status, _confirmed_members()),
    ):
        result = get_community_status("community-1")

    assert result is not None
    assert result["readiness_score"] == expected_score


def test_existing_community_without_members_returns_an_empty_status_model():
    with patch(
        "database.fetch_community_with_members",
        return_value=_row(FormationStatus.INTERESTED.value, None),
    ):
        result = get_community_status("community-1")

    assert result is not None
    assert result["members"] == []
    assert result["member_count"] == {"total": 0, "confirmed": 0, "invited": 0}
    assert result["readiness_score"] == 0


def test_unknown_community_is_reported_as_missing():
    with patch("database.fetch_community_with_members", return_value=None):
        result = get_community_status("community-1")

    assert result is None


def test_status_assembly_failure_is_reported_as_missing():
    with patch(
        "database.fetch_community_with_members",
        return_value={"community_id": "broken"},
    ):
        result = get_community_status("broken")

    assert result is None
