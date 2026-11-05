# SPDX-License-Identifier: AGPL-3.0-or-later
"""Replay immutable membership commands after separately confirming a join."""

from urllib.parse import parse_qs, urlsplit

import pytest

import vnb_exchange
from tests import test_community_create_postgres
from tests.test_dashboard_access_routes import _set_session
from tests.test_vnb_exchange import MutationAdapter

interest_database = test_community_create_postgres.interest_database
founder = test_community_create_postgres.founder
pytestmark = pytest.mark.integration


def test_http_join_replay_preserves_original_facts_and_does_not_redeliver(
    founder, monkeypatch
):
    """A real stored VNB acknowledgment remains replayable after local join."""
    client, db = founder
    assert db.save_building(
        "participant",
        "participant@example.org",
        {"address": "Synthetic 2", "lat": 47.2, "lon": 8.2, "bfs_number": 261},
        {"share_with_neighbors": True},
        verified=True,
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE consents SET share_with_neighbors=TRUE WHERE building_id='founder'"
        )
    _set_session(client, "founder")
    created = client.post(
        "/leg/community/create",
        data={"csrf_token": "csrf-secret", "name": "Synthetic replay LEG"},
    )
    assert created.status_code == 302
    cid = parse_qs(urlsplit(created.location).query)["cid"][0]
    assert (
        client.post(
            f"/leg/community/{cid}/invite",
            data={
                "csrf_token": "csrf-secret",
                "invite_email": "participant@example.org",
            },
        ).status_code
        == 302
    )
    adapter = MutationAdapter(frozenset({vnb_exchange.PARTICIPANT_JOIN}))
    monkeypatch.setitem(vnb_exchange.ADAPTERS, "manual-handover", adapter)
    fields = {
        "csrf_token": "csrf-secret",
        "mutation_id": "native-join-1",
        "participant_id": "participant",
        "mutation_type": "join",
        "effective_date": "2026-12-01",
        "source_agreement_id": "synthetic-agreement-1",
    }
    path = f"/leg/community/{cid}/vnb-mutations"
    assert client.post(path, data=fields).status_code == 302
    first = db.list_vnb_mutations(cid)[0]
    assert first["state"] == "acknowledged"
    assert first["before_facts"]["status"] == "invited"
    _set_session(client, "participant")
    assert (
        client.post(
            f"/leg/community/{cid}/confirm", data={"csrf_token": "csrf-secret"}
        ).status_code
        == 302
    )
    _set_session(client, "founder")
    assert client.post(path, data=fields).status_code == 302
    assert db.list_vnb_mutations(cid) == [first]
    assert adapter.calls == 1
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS count FROM vnb_mutation_events WHERE case_id=%s",
            (first["case_id"],),
        )
        assert cur.fetchone()["count"] == 1
        cur.execute(
            "SELECT COUNT(*) AS count FROM operator_events WHERE community_id=%s AND event_type='membership.mutation.acknowledged'",
            (cid,),
        )
        assert cur.fetchone()["count"] == 1
    for key, value in (
        ("participant_id", "founder"),
        ("mutation_type", "exit"),
        ("effective_date", "2026-12-02"),
        ("source_agreement_id", "different-agreement"),
    ):
        assert client.post(path, data={**fields, key: value}).status_code == 409
    assert (
        client.post(path, data={**fields, "mutation_id": "new-join"}).status_code == 409
    )
    _set_session(client, "participant")
    assert client.post(path, data=fields).status_code == 403
    _set_session(client, "founder")
    other = db.create_community_record("Other", "founder", "simple", "")
    assert (
        client.post(f"/leg/community/{other}/vnb-mutations", data=fields).status_code
        == 409
    )
    assert db.list_vnb_mutations(other) == []


@pytest.mark.parametrize("input_date", ["2026-12-01", "20261201"])
def test_claimed_join_resumes_from_recorded_facts_after_confirmation(
    founder, monkeypatch, input_date
):
    """Recover a committed claim whose first delivery was interrupted."""
    client, db = founder
    assert db.save_building(
        "participant",
        "participant@example.org",
        {"address": "Synthetic 2", "lat": 47.2, "lon": 8.2, "bfs_number": 261},
        {"share_with_neighbors": True},
        verified=True,
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE consents SET share_with_neighbors=TRUE WHERE building_id='founder'"
        )
    cid = db.create_community_record("Synthetic recovery", "founder", "simple", "")
    assert db.insert_invited_member(cid, "participant", "founder")

    class InterruptedAdapter(MutationAdapter):
        def __init__(self, capabilities):
            super().__init__(capabilities)
            self.interrupted = False
            self.keys = []

        def submit_mutation(self, mutation, *, idempotency_key):
            """Interrupt before transport once and preserve both delivery keys."""
            self.keys.append(idempotency_key)
            if not self.interrupted:
                self.interrupted = True
                raise KeyboardInterrupt("stop before external delivery")
            return super().submit_mutation(mutation, idempotency_key=idempotency_key)

    adapter = InterruptedAdapter(frozenset({vnb_exchange.PARTICIPANT_JOIN}))
    monkeypatch.setitem(vnb_exchange.ADAPTERS, "manual-handover", adapter)
    monkeypatch.setitem(vnb_exchange.ADAPTERS, "test-vnb", adapter)
    _set_session(client, "founder")
    fields = {
        "csrf_token": "csrf-secret",
        "mutation_id": "interrupted-join",
        "participant_id": "participant",
        "mutation_type": "join",
        "effective_date": input_date,
        "source_agreement_id": "synthetic-agreement-1",
    }
    path = f"/leg/community/{cid}/vnb-mutations"
    with pytest.raises(KeyboardInterrupt, match="before external"):
        client.post(path, data=fields)
    first = db.list_vnb_mutations(cid)[0]
    assert first["state"] == "claimed"
    _set_session(client, "participant")
    assert (
        client.post(
            f"/leg/community/{cid}/confirm", data={"csrf_token": "csrf-secret"}
        ).status_code
        == 302
    )
    _set_session(client, "founder")
    assert client.post(path, data=fields).status_code == 302
    recovered = db.list_vnb_mutations(cid)[0]
    assert recovered["state"] == "acknowledged"
    assert recovered["case_id"] == first["case_id"]
    assert recovered["before_facts"] == first["before_facts"]
    assert recovered["after_facts"] == first["after_facts"]
    assert adapter.calls == 1
    assert len(adapter.keys) == 2 and adapter.keys[0] == adapter.keys[1]


def test_changed_roles_do_not_resnapshot_a_recorded_change_proposal(
    founder, monkeypatch
):
    """Replay requested changes against original facts rather than later roles."""
    client, db = founder
    assert db.save_building(
        "participant",
        "participant@example.org",
        {"address": "Synthetic 2", "lat": 47.2, "lon": 8.2, "bfs_number": 261},
        {"share_with_neighbors": True},
        verified=True,
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE consents SET share_with_neighbors=TRUE WHERE building_id='founder'"
        )
    cid = db.create_community_record("Synthetic role change", "founder", "simple", "")
    assert db.insert_invited_member(cid, "participant", "founder")
    assert db.confirm_invited_member(cid, "participant")
    _set_session(client, "founder")
    adapter = MutationAdapter(frozenset({vnb_exchange.PARTICIPANT_CHANGE}))
    monkeypatch.setitem(vnb_exchange.ADAPTERS, "manual-handover", adapter)
    fields = {
        "csrf_token": "csrf-secret",
        "mutation_id": "change-1",
        "participant_id": "participant",
        "mutation_type": "change",
        "effective_date": "2026-12-01",
        "source_agreement_id": "synthetic-agreement-1",
        "after_facts": '{"access_roles":["auditor"]}',
    }
    path = f"/leg/community/{cid}/vnb-mutations"
    assert client.post(path, data=fields).status_code == 302
    first = db.list_vnb_mutations(cid)[0]
    assert (
        client.post(
            f"/leg/community/{cid}/members/participant/roles",
            data={"csrf_token": "csrf-secret", "roles": "metering"},
        ).status_code
        == 302
    )
    assert client.post(path, data=fields).status_code == 302
    assert (
        client.post(
            path, data={**fields, "after_facts": '{"access_roles":["documents"]}'}
        ).status_code
        == 409
    )
    assert db.list_vnb_mutations(cid) == [first]
    assert adapter.calls == 1
    members = db.fetch_community_with_members(cid, "founder")["members"]
    participant = next(item for item in members if item["building_id"] == "participant")
    assert participant["access_roles"] == ["metering"]
    assert first["after_facts"]["access_roles"] == ["auditor"]
