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
        (
            community_access.METERING,
            community_access.VIEW_COMMUNITY,
            community_access.MANAGE_MEMBERS,
        ),
        (
            community_access.METERING,
            community_access.MANAGE_METERING,
            community_access.PREPARE_BILLING,
        ),
        (
            community_access.METERING,
            community_access.VIEW_COMMUNITY,
            community_access.APPROVE_BILLING,
        ),
        (
            community_access.METERING,
            community_access.MANAGE_METERING,
            community_access.AUDIT_BILLING,
        ),
        (
            community_access.AUDITOR,
            community_access.VIEW_COMMUNITY,
            community_access.MANAGE_MEMBERS,
        ),
        (
            community_access.AUDITOR,
            community_access.VIEW_COMMUNITY,
            community_access.MANAGE_DOCUMENTS,
        ),
        (
            community_access.AUDITOR,
            community_access.AUDIT_BILLING,
            community_access.MANAGE_METERING,
        ),
        (
            community_access.AUDITOR,
            community_access.AUDIT_BILLING,
            community_access.PREPARE_BILLING,
        ),
        (
            community_access.AUDITOR,
            community_access.AUDIT_BILLING,
            community_access.APPROVE_BILLING,
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
        lambda _period_id: {"prepared_by": "b1"},
    )

    result = dashboard.leg_approve_billing_period("c1", "b1", 42)

    assert result["invoices"] == []
    assert "verschiedene Personen" in result["error"]


def test_dual_control_refuses_a_period_without_a_recorded_preparer(monkeypatch):
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
    monkeypatch.setattr(dashboard.db, "get_billing_period", lambda _period_id: {})

    result = dashboard.leg_approve_billing_period("c1", "b2", 42)

    assert result["invoices"] == []
    assert "Vorbereitung" in result["error"]


def test_empty_operator_query_update_is_rejected(monkeypatch):
    monkeypatch.setattr(
        dashboard,
        "_require_capability",
        lambda *_args: member(community_access.BILLING_PREPARER),
    )

    result = dashboard.operator_update_invoice_query("c1", "b1", 7)

    assert result["error"]


def test_leg_set_member_roles_refuses_without_manage_members_and_passes_through(
    monkeypatch,
):
    monkeypatch.setattr(dashboard, "_require_capability", lambda *_args: None)

    refused = dashboard.leg_set_member_roles("c1", "b1", "b2", ["documents"])

    assert refused == {"error": "Nur die Administration kann Rollen ändern."}

    calls = []

    def _fake_set_roles(community_id, target_building_id, roles, actor_building_id):
        calls.append((community_id, target_building_id, roles, actor_building_id))
        return True

    monkeypatch.setattr(
        dashboard,
        "_require_capability",
        lambda *_args: member(community_access.MEMBERSHIP),
    )
    monkeypatch.setattr(dashboard.db, "set_member_access_roles", _fake_set_roles)

    result = dashboard.leg_set_member_roles("c1", "b1", "b2", ["documents"])

    assert result == {"error": None}
    assert calls == [("c1", "b2", ["documents"], "b1")]


def test_leg_set_dual_control_requires_an_administrator(monkeypatch):
    monkeypatch.setattr(
        dashboard,
        "_require_capability",
        lambda *_args: member(community_access.DOCUMENTS),
    )

    refused = dashboard.leg_set_dual_control("c1", "b1", True)

    assert refused == {
        "error": "Nur die Administration kann das Vier-Augen-Prinzip ändern."
    }

    calls = []

    def _fake_set_dual_control(community_id, enabled, actor_building_id):
        calls.append((community_id, enabled, actor_building_id))
        return True

    monkeypatch.setattr(
        dashboard,
        "_require_capability",
        lambda *_args: member(community_access.ADMIN),
    )
    monkeypatch.setattr(
        dashboard.db, "set_community_dual_control", _fake_set_dual_control
    )

    result = dashboard.leg_set_dual_control("c1", "b1", True)

    assert result == {"error": None}
    assert calls == [("c1", True, "b1")]


def test_prepare_billing_period_records_the_confirmed_preparer(monkeypatch):
    calls = []

    monkeypatch.setattr(
        dashboard,
        "_require_capability",
        lambda *_args: member(community_access.BILLING_PREPARER),
    )
    monkeypatch.setattr(
        dashboard.db,
        "record_billing_period_preparer",
        lambda period_id, community_id, actor: (
            calls.append((period_id, community_id, actor)) or True
        ),
    )

    result = dashboard.leg_prepare_billing_period("c1", "b9", 42)

    assert result == {"error": None}
    assert calls == [(42, "c1", "b9")]


def test_prepare_billing_period_refuses_without_prepare_capability(monkeypatch):
    monkeypatch.setattr(
        dashboard,
        "_require_capability",
        lambda *_args: None,
    )

    result = dashboard.leg_prepare_billing_period("c1", "b9", 42)

    assert result["error_status"] == 403


def test_prepare_billing_period_refuses_a_non_draft_period(monkeypatch):
    monkeypatch.setattr(
        dashboard,
        "_require_capability",
        lambda *_args: member(community_access.BILLING_PREPARER),
    )
    monkeypatch.setattr(
        dashboard.db,
        "record_billing_period_preparer",
        lambda _period_id, _community_id, _actor: False,
    )

    result = dashboard.leg_prepare_billing_period("c1", "b9", 42)

    assert result["error_status"] == 409
