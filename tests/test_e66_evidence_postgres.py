# SPDX-License-Identifier: AGPL-3.0-or-later
"""Import external E66 identities through the authenticated evidence boundary."""

import pytest

from tests import test_calculated_values_lifecycle_postgres as lifecycle

interest_database = lifecycle.interest_database
calculated_client = lifecycle.calculated_client
pytestmark = pytest.mark.integration


@pytest.fixture
def assignment_request(calculated_client):
    client, db, payload = calculated_client
    point = "SYNTHETIC-METER-1"
    assert (
        db.upsert_metering_points(
            [{"metering_point_id": point, "vnb_community_id": "SYNTHETIC-EXTERNAL-LEG"}]
        )
        == 1
    )
    return (
        client,
        db,
        payload,
        point,
        {
            "effective_from": "2026-09-01T00:00:00+02:00",
            "effective_until": "2026-11-01T00:00:00+01:00",
            "source_reference": "SYNTHETIC-MEMBERSHIP-STATEMENT",
            "confirmed_membership": True,
        },
    )


def test_operator_can_record_effective_validity_for_an_existing_meter_assignment(
    calculated_client,
):
    client, db, payload = calculated_client
    point = "SYNTHETIC-METER-1"
    assert (
        db.upsert_metering_points(
            [{"metering_point_id": point, "vnb_community_id": "SYNTHETIC-EXTERNAL-LEG"}]
        )
        == 1
    )

    response = client.post(
        f"/admin/vnb-calculated-values/dietikon/mappings/{point}",
        headers={"X-Admin-Token": "synthetic-admin"},
        json={
            "effective_from": "2026-09-01T00:00:00+02:00",
            "effective_until": "2026-11-01T00:00:00+01:00",
            "source_reference": "SYNTHETIC-MEMBERSHIP-STATEMENT",
            "confirmed_membership": True,
        },
    )

    assert response.status_code == 201
    assert response.headers["Cache-Control"] == "no-store"
    stored = db.get_metering_point(point)
    assert stored["community_id"] == payload["community_id"]
    assert stored["building_id"] == "member"
    assert (
        stored["assignment_effective_from"].isoformat() == "2026-08-31T22:00:00+00:00"
    )
    assert (
        stored["assignment_effective_until"].isoformat() == "2026-10-31T23:00:00+00:00"
    )
    assert stored["assignment_reference"] == "SYNTHETIC-MEMBERSHIP-STATEMENT"
    assert stored["assignment_actor"] == "admin-token"
    assert stored["assignment_recorded_at"] is not None


@pytest.mark.parametrize(
    "token,territory,expected",
    [
        (None, "dietikon", 403),
        ("wrong", "dietikon", 403),
        ("synthetic-admin", "baden", 404),
    ],
)
def test_assignment_statement_is_private_and_tenant_scoped(
    assignment_request, token, territory, expected
):
    client, db, _, point, payload = assignment_request
    response = client.post(
        f"/admin/vnb-calculated-values/{territory}/mappings/{point}",
        headers={"X-Admin-Token": token} if token else {},
        json=payload,
    )
    assert response.status_code == expected
    assert response.headers["Cache-Control"] == "no-store"
    assert db.get_metering_point(point)["assignment_reference"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"confirmed_membership": False},
        {"confirmed_membership": "true"},
        {"source_reference": ""},
        {"source_reference": "x" * 129},
        {"effective_from": "2026-09-01"},
        {"effective_from": None},
        {"effective_until": "2026-08-01T00:00:00Z"},
        {"effective_until": "not-a-date"},
    ],
)
def test_invalid_assignment_statement_is_not_recorded(assignment_request, changes):
    client, db, _, point, payload = assignment_request
    response = client.post(
        f"/admin/vnb-calculated-values/dietikon/mappings/{point}",
        headers={"X-Admin-Token": "synthetic-admin"},
        json={**payload, **changes},
    )
    assert response.status_code == 400
    assert db.get_metering_point(point)["assignment_reference"] is None


@pytest.mark.parametrize(
    "incomplete", ["external_community", "directions", "inactive", "unconfirmed"]
)
def test_incomplete_registry_binding_cannot_gain_a_validity_statement(
    assignment_request, incomplete
):
    client, db, _, point, payload = assignment_request
    with db.get_connection() as conn, conn.cursor() as cur:
        if incomplete == "external_community":
            cur.execute("UPDATE metering_points SET vnb_community_id = NULL")
        elif incomplete == "directions":
            cur.execute("UPDATE metering_points SET expected_directions = NULL")
        elif incomplete == "inactive":
            cur.execute("UPDATE metering_points SET active = FALSE")
        else:
            cur.execute("UPDATE community_members SET status = 'invited'")
    response = client.post(
        f"/admin/vnb-calculated-values/dietikon/mappings/{point}",
        headers={"X-Admin-Token": "synthetic-admin"},
        json=payload,
    )
    assert response.status_code == 404
    assert db.get_metering_point(point)["assignment_reference"] is None


@pytest.mark.parametrize(
    "field,value,invalidates",
    [
        ("vnb_community_id", "SYNTHETIC-OTHER-LEG", True),
        ("building_id", "other-member", True),
        ("expected_directions", ["production"], True),
        ("alias", "Synthetic renamed meter", False),
    ],
)
def test_binding_changes_invalidate_the_old_statement(
    assignment_request, field, value, invalidates
):
    client, db, community, point, payload = assignment_request
    assert db.save_building(
        "other-member",
        "other@example.invalid",
        {"address": "Synthetic other", "lat": 47.2, "lon": 8.2},
        {},
        verified=True,
    )
    assert db.insert_invited_member(community["community_id"], "other-member", "member")
    assert db.confirm_invited_member(community["community_id"], "other-member")
    assert (
        client.post(
            f"/admin/vnb-calculated-values/dietikon/mappings/{point}",
            headers={"X-Admin-Token": "synthetic-admin"},
            json=payload,
        ).status_code
        == 201
    )
    assert db.upsert_metering_points([{"metering_point_id": point, field: value}]) == 1
    stored = db.get_metering_point(point)
    for key in (
        "assignment_effective_from",
        "assignment_effective_until",
        "assignment_reference",
        "assignment_actor",
        "assignment_recorded_at",
    ):
        if invalidates:
            assert stored[key] is None
        else:
            assert stored[key] is not None


def test_assignment_retry_keeps_the_original_statement_time(assignment_request):
    client, db, _, point, payload = assignment_request
    endpoint = f"/admin/vnb-calculated-values/dietikon/mappings/{point}"
    headers = {"X-Admin-Token": "synthetic-admin"}
    assert client.post(endpoint, headers=headers, json=payload).status_code == 201
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE metering_points SET assignment_recorded_at = '2000-01-01T00:00:00Z'"
        )
    original = db.get_metering_point(point)["assignment_recorded_at"]
    assert client.post(endpoint, headers=headers, json=payload).status_code == 201
    assert db.get_metering_point(point)["assignment_recorded_at"] == original
