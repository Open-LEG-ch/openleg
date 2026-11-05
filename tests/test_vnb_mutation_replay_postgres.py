# SPDX-License-Identifier: AGPL-3.0-or-later
"""Replay immutable membership commands after separately confirming a join."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from urllib.parse import parse_qs, urlsplit

import pytest

import vnb_exchange
from tests import test_community_create_postgres
from tests.test_dashboard_access_routes import _set_session
from tests.test_vnb_exchange import MutationAdapter

interest_database = test_community_create_postgres.interest_database
founder = test_community_create_postgres.founder
pytestmark = pytest.mark.integration


def test_waiting_replay_reads_the_event_committed_with_its_final_case(
    founder, monkeypatch
):
    """A replay blocked on the case lock observes the outcome's actual event."""
    from store import vnb_exchange as cases

    _, db = founder
    cid = db.create_community_record(
        "Synthetic concurrent replay", "founder", "simple", ""
    )
    claim = vnb_exchange.MutationClaim(
        mutation_id="concurrent-event-reference",
        community_id=cid,
        participant_id="founder",
        actor_building_id="founder",
        mutation_type="change",
        effective_date="2026-12-01",
        source_agreement_id="synthetic-agreement",
        before={"access_roles": []},
        after={"access_roles": ["auditor"]},
        adapter_key="manual-handover",
        contract_version="vnb-membership/1",
        capability_snapshot=(),
        payload_fingerprint="f" * 64,
    )
    original = cases.claim_mutation(claim)
    outcome = vnb_exchange.MutationOutcome(
        case_id=original["case_id"],
        mutation_id=claim.mutation_id,
        state="prepared",
        contract_version=claim.contract_version,
        payload_fingerprint=claim.payload_fingerprint,
        next_action="manual_handover",
        manual_package=b"synthetic package",
    )
    inserted, release, connected = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    enqueue = cases.enqueue_event
    connection = cases._get_connection
    replay_pid = []

    def hold_outcome(*args, **kwargs):
        event_id = enqueue(*args, **kwargs)
        inserted.set()
        assert release.wait(10), "outcome transaction was not released"
        return event_id

    @contextmanager
    def observe_connection():
        with connection() as conn:
            if threading.current_thread().name.startswith("mutation-replay"):
                replay_pid.append(conn.get_backend_pid())
                connected.set()
            yield conn

    monkeypatch.setattr(cases, "enqueue_event", hold_outcome)
    monkeypatch.setattr(cases, "_get_connection", observe_connection)
    with (
        ThreadPoolExecutor(max_workers=1) as finalizer,
        ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="mutation-replay"
        ) as replayer,
    ):
        finished = finalizer.submit(
            cases.record_mutation_outcome, original["case_id"], outcome
        )
        try:
            assert inserted.wait(5), "outcome did not reach its uncommitted event"
            replay = replayer.submit(cases.claim_mutation, claim)
            assert connected.wait(5), "replay did not connect"
            deadline = time.monotonic() + 5
            with db.get_connection() as conn, conn.cursor() as cur:
                while True:
                    cur.execute(
                        "SELECT pg_blocking_pids(%s) AS blockers", (replay_pid[0],)
                    )
                    if cur.fetchone()["blockers"]:
                        break
                    assert time.monotonic() < deadline, (
                        "replay did not wait on the case lock"
                    )
                    time.sleep(0.01)
        finally:
            release.set()
        recorded = finished.result(timeout=5)
        replayed = replay.result(timeout=5)
    assert replayed["state"] == recorded["state"] == "prepared"
    assert replayed["event_id"] == recorded["event_id"]


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
@pytest.mark.parametrize("legacy_writer", [False, True])
def test_claimed_join_resumes_from_recorded_facts_after_confirmation(
    founder, monkeypatch, input_date, legacy_writer
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
        if legacy_writer:
            # Before date normalization, the trusted delivery seam fingerprinted
            # the raw compact date while PostgreSQL stored an ISO DATE.
            from store import vnb_exchange as mutation_cases

            participant = next(
                member
                for member in db.fetch_community_with_members(cid)["members"]
                if member["building_id"] == "participant"
            )
            before = {
                key: participant[key]
                for key in ("building_id", "status", "role", "access_roles")
                if key in participant
            }
            vnb_exchange.submit_participant_mutation(
                vnb_exchange.ParticipantMutation(
                    mutation_id=fields["mutation_id"],
                    community_id=cid,
                    participant_id="participant",
                    mutation_type="join",
                    effective_date=input_date,
                    source_agreement_id=fields["source_agreement_id"],
                    before=before,
                    after={**before, "status": "confirmed"},
                ),
                "founder",
                adapter,
                mutation_cases,
            )
        else:
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
    equivalent = {"access_roles": ["auditor"], "role": first["before_facts"]["role"]}
    assert (
        client.post(
            path, data={**fields, "after_facts": json.dumps(equivalent)}
        ).status_code
        == 302
    )
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


@pytest.mark.parametrize("acknowledged", [False, True])
def test_operator_replay_retains_the_actual_recorded_event_reference(
    founder, monkeypatch, acknowledged
):
    """A final API outcome retains its real event without creating another one."""
    client, db = founder
    _set_session(client, "founder")
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE consents SET share_with_neighbors=TRUE WHERE building_id='founder'"
        )
    cid = db.create_community_record("Synthetic event replay", "founder", "simple", "")
    adapter = MutationAdapter(
        frozenset({vnb_exchange.PARTICIPANT_CHANGE}) if acknowledged else frozenset()
    )
    monkeypatch.setitem(vnb_exchange.ADAPTERS, "manual-handover", adapter)
    issued = client.post(
        f"/leg/community/{cid}/operator-api/credentials",
        json={
            "name": "Synthetic replay operator",
            "capabilities": ["membership.read", "membership.mutate"],
        },
        headers={"X-CSRF-Token": "csrf-secret"},
    )
    assert issued.status_code == 201
    headers = {"Authorization": "Bearer " + issued.json["token"]}
    fields = {
        "mutation_id": "api-event-reference",
        "participant_id": "founder",
        "mutation_type": "change",
        "effective_date": "2026-12-01",
        "source_agreement_id": "synthetic-agreement",
        "after": {"access_roles": ["auditor"]},
    }
    path = f"/api/operator/v1/communities/{cid}/membership-mutations"
    first = client.post(path, json=fields, headers=headers)
    assert first.status_code == 202 and first.json["event_id"]
    second = client.post(path, json=fields, headers=headers)
    assert second.status_code == 202 and second.json == first.json
    stored = db.get_vnb_mutation(cid, fields["mutation_id"])
    assert stored["event_id"] == first.json["event_id"]
    from store import vnb_exchange as cases

    replay = vnb_exchange.submit_participant_mutation(
        vnb_exchange.ParticipantMutation(
            mutation_id=fields["mutation_id"],
            community_id=cid,
            participant_id="founder",
            mutation_type="change",
            effective_date=fields["effective_date"],
            source_agreement_id=fields["source_agreement_id"],
            before=stored["before_facts"],
            after=stored["after_facts"],
        ),
        "founder",
        adapter,
        cases,
    )
    assert replay.event_id == first.json["event_id"]
    assert adapter.calls == int(acknowledged)
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS count FROM operator_events WHERE community_id=%s AND event_id=%s",
            (cid, first.json["event_id"]),
        )
        assert cur.fetchone()["count"] == 1


@pytest.mark.parametrize(
    "field,original,replacement", [("role", 1, True), ("access_roles", [1], [True])]
)
def test_replay_preserves_json_value_types(
    founder, monkeypatch, field, original, replacement
):
    """Accepted JSON values must not alias through Python numeric equality."""
    client, db = founder
    with db.get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            "UPDATE consents SET share_with_neighbors=TRUE WHERE building_id='founder'"
        )
    cid = db.create_community_record("Synthetic typed replay", "founder", "simple", "")
    adapter = MutationAdapter(frozenset({vnb_exchange.PARTICIPANT_CHANGE}))
    monkeypatch.setitem(vnb_exchange.ADAPTERS, "manual-handover", adapter)
    _set_session(client, "founder")
    fields = {
        "csrf_token": "csrf-secret",
        "mutation_id": "typed-replay",
        "participant_id": "founder",
        "mutation_type": "change",
        "effective_date": "2026-12-01",
        "source_agreement_id": "synthetic-types",
        "after_facts": json.dumps({field: original}),
    }
    path = f"/leg/community/{cid}/vnb-mutations"
    assert client.post(path, data=fields).status_code == 302
    first = db.list_vnb_mutations(cid)
    assert client.post(path, data=fields).status_code == 302
    assert (
        client.post(
            path, data={**fields, "after_facts": json.dumps({field: replacement})}
        ).status_code
        == 409
    )
    assert db.list_vnb_mutations(cid) == first
    assert adapter.calls == 1
