# SPDX-License-Identifier: AGPL-3.0-or-later
"""Domain contracts for formation state transitions (formation_wizard).

The wizard owns the formation rules: the minimum size gate, the DSO submission
transition, and the shape handed to dashboard callers.
"""

from unittest.mock import patch

import formation_wizard
from formation_wizard import (
    FormationStatus,
    confirm_membership,
    create_community,
    invite_member,
    start_formation,
    submit_to_dso,
)


def test_create_community_reports_the_initial_state():
    with patch(
        "database.create_community_record", return_value="community-1"
    ) as record:
        created = create_community("LEG Musterweg", "b-admin", "proportional")

    record.assert_called_once_with("LEG Musterweg", "b-admin", "proportional", "")
    assert created == {
        "community_id": "community-1",
        "name": "LEG Musterweg",
        "admin_building_id": "b-admin",
        "distribution_model": "proportional",
        "status": FormationStatus.INTERESTED.value,
        "member_count": 1,
    }


def test_create_community_without_a_record_reports_failure():
    with patch("database.create_community_record", return_value=None):
        assert create_community("LEG", "b-admin") is None


def test_create_community_defaults_to_the_simple_distribution_model():
    with patch(
        "database.create_community_record", return_value="community-1"
    ) as record:
        assert create_community("LEG", "b-admin") is not None

    record.assert_called_once_with("LEG", "b-admin", "simple", "")


def test_invite_and_confirm_delegate_to_the_repository():
    with (
        patch("database.insert_invited_member", return_value=True) as invite,
        patch("database.confirm_invited_member", return_value=True) as confirm,
    ):
        assert invite_member("c1", "b2", "b1") is True
        assert confirm_membership("c1", "b2") is True

    invite.assert_called_once_with("c1", "b2", "b1")
    confirm.assert_called_once_with("c1", "b2")


def test_start_formation_blocks_below_the_minimum_community_size(caplog):
    with (
        patch("database.count_confirmed_members", return_value=2),
        patch("database.mark_formation_started") as mark,
    ):
        assert start_formation("c1") is False

    mark.assert_not_called()
    assert caplog.messages == ["[FORMATION] Community c1 has only 2 members, need 3"]
    assert caplog.records[0].levelname == "WARNING"


def test_start_formation_blocks_when_the_count_cannot_be_read(caplog):
    with (
        patch("database.count_confirmed_members", return_value=None),
        patch("database.mark_formation_started") as mark,
    ):
        assert start_formation("c1") is False

    mark.assert_not_called()
    assert caplog.messages == ["[FORMATION] Could not count members for community c1"]
    assert caplog.records[0].levelname == "ERROR"


def test_start_formation_passes_at_the_minimum_community_size():
    with (
        patch("database.count_confirmed_members", return_value=3) as count,
        patch("database.mark_formation_started", return_value=True) as mark,
    ):
        assert start_formation("c1") is True

    count.assert_called_once_with("c1")
    mark.assert_called_once_with("c1")


def test_start_formation_propagates_a_declined_transition():
    """The store refuses invalid or already-started states with False."""
    with (
        patch("database.count_confirmed_members", return_value=3),
        patch("database.mark_formation_started", return_value=False) as mark,
    ):
        assert start_formation("c1") is False

    mark.assert_called_once_with("c1")


def test_submit_to_dso_delegates_the_transition_guard_to_the_repository():
    with patch("database.submit_community_to_dso", return_value=False) as submit:
        assert submit_to_dso("c1") is False

    submit.assert_called_once_with("c1")


def test_get_user_communities_falls_back_to_an_empty_list():
    with patch("database.fetch_user_communities", return_value=None) as read:
        assert formation_wizard.get_user_communities("b1") == []

    read.assert_called_once_with("b1")
