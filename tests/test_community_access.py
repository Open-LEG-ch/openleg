# SPDX-License-Identifier: AGPL-3.0-or-later

import pytest

import community_access
import dashboard


def member(*roles, status="confirmed", legacy_role="member"):
    return {
        "building_id": "b1",
        "role": legacy_role,
        "access_roles": list(roles),
        "status": status,
    }


def test_legacy_admin_retains_all_capabilities():
    legacy = member(legacy_role="admin")

    for capability in (
        community_access.MANAGE_MEMBERS,
        community_access.MANAGE_DOCUMENTS,
        community_access.MANAGE_METERING,
        community_access.PREPARE_BILLING,
        community_access.APPROVE_BILLING,
        community_access.AUDIT_BILLING,
    ):
        assert community_access.allows(legacy, capability)


@pytest.mark.parametrize(
    ("role", "allowed", "denied"),
    [
        (
            community_access.MEMBERSHIP,
            community_access.MANAGE_MEMBERS,
            community_access.MANAGE_DOCUMENTS,
        ),
        (
            community_access.DOCUMENTS,
            community_access.MANAGE_DOCUMENTS,
            community_access.MANAGE_MEMBERS,
        ),
        (
            community_access.BILLING_PREPARER,
            community_access.PREPARE_BILLING,
            community_access.APPROVE_BILLING,
        ),
        (
            community_access.BILLING_APPROVER,
            community_access.APPROVE_BILLING,
            community_access.PREPARE_BILLING,
        ),
    ],
)
def test_scoped_roles_grant_only_their_capabilities(role, allowed, denied):
    scoped = member(role)

    assert community_access.allows(scoped, allowed)
    assert not community_access.allows(scoped, denied)


def test_unconfirmed_member_has_no_operational_capability():
    assert not community_access.allows(
        member(community_access.ADMIN, status="invited"),
        community_access.MANAGE_MEMBERS,
    )


def test_role_validation_is_canonical_and_fail_closed():
    assert community_access.validate_roles(
        [community_access.DOCUMENTS, community_access.ADMIN, community_access.DOCUMENTS]
    ) == (community_access.ADMIN, community_access.DOCUMENTS)
    with pytest.raises(ValueError):
        community_access.validate_roles(["owner"])


def test_capabilities_for_exposes_the_complete_scoped_interface():
    scoped = member(community_access.BILLING_PREPARER, community_access.DOCUMENTS)

    assert community_access.capabilities_for(scoped) == frozenset(
        {
            community_access.VIEW_COMMUNITY,
            community_access.MANAGE_DOCUMENTS,
            community_access.PREPARE_BILLING,
            community_access.AUDIT_BILLING,
        }
    )


def test_dual_control_refuses_the_person_who_prepared_the_period(monkeypatch):
    monkeypatch.setattr(
        dashboard,
        "_require_capability",
        lambda *_args: member(community_access.BILLING_APPROVER),
    )
    monkeypatch.setattr(
        dashboard.formation_wizard,
        "get_community_status",
        lambda _community_id: {"require_dual_control": True},
    )
    monkeypatch.setattr(
        dashboard.db,
        "get_billing_period",
        lambda _period_id: {"community_id": "c1", "prepared_by": "b1"},
    )

    result = dashboard.leg_approve_billing_period("c1", "b1", 42)

    assert result["invoices"] == []
    assert "andere Person" in result["error"]


@pytest.mark.parametrize(
    "period",
    [
        {"community_id": "c1", "prepared_by": None},
        {"community_id": "c1", "prepared_by": "system"},
        {"community_id": "other", "prepared_by": "b2"},
    ],
)
def test_dual_control_refuses_unattributed_or_wrong_community_period(
    monkeypatch, period
):
    monkeypatch.setattr(
        dashboard,
        "_require_capability",
        lambda *_args: member(community_access.BILLING_APPROVER),
    )
    monkeypatch.setattr(
        dashboard.formation_wizard,
        "get_community_status",
        lambda _community_id: {"require_dual_control": True},
    )
    monkeypatch.setattr(dashboard.db, "get_billing_period", lambda _period_id: period)

    result = dashboard.leg_approve_billing_period("c1", "b1", 42)

    assert result["invoices"] == []
    assert "dokumentierte Vorbereitung" in result["error"]
