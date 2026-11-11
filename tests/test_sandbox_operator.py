# SPDX-License-Identifier: AGPL-3.0-or-later
"""The browser console uses real scoped APIs and signed local delivery."""

import hashlib
import json
from uuid import uuid4

import pytest

import operator_api
from tests import test_sandbox_http
from tests.test_sandbox_metering import _workspace_connection
from tests.test_sandbox_personas import _start, _switch

village = test_sandbox_http.village
_csrf = test_sandbox_http._csrf
pytestmark = pytest.mark.integration
COMMUNITY = "sonnwil-operating-leg"
CAPABILITIES = ["formation.read", "membership.read", "membership.mutate"]
MUTATION = {
    "mutation_id": "sonnwil-demo-mutation-1",
    "participant_id": "sonnwil-resident",
    "mutation_type": "change",
    "effective_date": "2026-11-10",
    "source_agreement_id": "SIMULATION-SONNWIL-AGREEMENT-1",
    "after": {"access_roles": ["metering"]},
}


def _credential(client, prefix, **fields):
    guide = client.get(prefix + "/sandbox/operator")
    return client.post(
        prefix + f"/leg/community/{COMMUNITY}/operator-api/credentials",
        json={
            "name": "SIMULATION Sonnwil Integration",
            "capabilities": CAPABILITIES,
            "webhook_url": operator_api.SANDBOX_WEBHOOK_URL,
            **fields,
        },
        headers={"X-CSRF-Token": _csrf(guide)},
    )


def _dispatch(client, prefix, mode):
    page = client.get(prefix + "/sandbox/operator")
    return client.post(
        prefix + "/sandbox/operator/dispatch",
        data={"csrf_token": _csrf(page), "confirm": "yes", "mode": mode},
    )


def _foreign_pending(village, prefix):
    """Prepare a pending webhook outside the exercise community."""
    client, event, delivery = (str(uuid4()) for _ in range(3))
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cur,
    ):
        cur.execute(
            "INSERT INTO operator_api_clients(id,community_id,created_by,name,capabilities,token_hash,webhook_url) VALUES (%s,'sonnwil-private-leg','sonnwil-private','FOREIGN-CLIENT','[\"membership.read\"]',%s,%s)",
            (
                client,
                hashlib.sha256(client.encode()).hexdigest(),
                operator_api.SANDBOX_WEBHOOK_URL,
            ),
        )
        cur.execute(
            "INSERT INTO operator_events(event_id,event_type,schema_version,aggregate_id,community_id,payload) VALUES (%s,'membership.mutation.prepared','operator-event/1','FOREIGN-AGGREGATE','sonnwil-private-leg','{\"private\":\"FOREIGN-PAYLOAD\"}')",
            (event,),
        )
        cur.execute(
            "INSERT INTO operator_webhook_deliveries(delivery_id,event_id,client_id) VALUES (%s,%s,%s)",
            (delivery, event, client),
        )
    return delivery


def test_real_operator_requests_replay_and_signed_capture_retry(village):
    """Real operator requests retain keyed replay and signed webhook retry behavior."""
    client = village.test_client()
    prefix, _ = _start(client, "administrator")
    assert client.get(prefix + "/sandbox/operator").status_code == 200
    foreign = _foreign_pending(village, prefix)
    issued = _credential(client, prefix)
    assert issued.status_code == 201
    issued = issued.json
    token, secret, client_id = (
        issued["token"],
        issued["webhook_secret"],
        issued["credential"]["id"],
    )
    api = prefix + f"/api/operator/v1/communities/{COMMUNITY}"
    headers = {"Authorization": "Bearer " + token}
    assert client.get(api + "/formation", headers=headers).status_code == 200
    assert (
        client.get(
            prefix + "/api/operator/v1/communities/sonnwil-private-leg/formation",
            headers=headers,
        ).status_code
        == 404
    )
    assert (
        client.post(
            api + "/formation/submissions", json={}, headers=headers
        ).status_code
        == 403
    )
    first = client.post(api + "/membership-mutations", json=MUTATION, headers=headers)
    assert first.status_code == 202
    assert (
        client.post(api + "/membership-mutations", json=MUTATION, headers=headers).json
        == first.json
    )
    changed = {**MUTATION, "after": {"access_roles": ["documents"]}}
    assert (
        client.post(
            api + "/membership-mutations", json=changed, headers=headers
        ).status_code
        == 409
    )
    assert (
        client.get(api + "/membership-mutations", headers=headers).json["mutations"][0][
            "state"
        ]
        == "prepared"
    )
    assert _dispatch(client, prefix, "fail").status_code == 303
    captures = client.get(prefix + "/sandbox/operator/captures").json["captures"]
    assert (
        len(captures) == 1
        and captures[0]["verified"]
        and captures[0]["response_status"] == 503
    )
    assert operator_api.verify_webhook_signature(
        captures[0]["body"].encode(), captures[0]["signature"], secret
    )
    payload = json.loads(captures[0]["body"])
    assert (
        payload["event_id"] == first.json["event_id"]
        and payload["event_type"] == "membership.mutation.prepared"
    )
    delivery = captures[0]["delivery_id"]
    guide = client.get(prefix + "/sandbox/operator")
    retried = client.post(
        prefix + f"/leg/community/{COMMUNITY}/operator-api/deliveries/{delivery}/retry",
        json={},
        headers={"X-CSRF-Token": _csrf(guide)},
    )
    assert retried.status_code == 200
    assert _dispatch(client, prefix, "deliver").status_code == 303
    captures = client.get(prefix + "/sandbox/operator/captures").json["captures"]
    assert len(captures) == 2 and {row["attempt"] for row in captures} == {1, 2}
    assert all(
        row["verified"]
        and operator_api.verify_webhook_signature(
            row["body"].encode(), row["signature"], secret
        )
        for row in captures
    )
    assert {row["response_status"] for row in captures} == {503, 204}
    assert _dispatch(client, prefix, "deliver").status_code == 303
    assert len(client.get(prefix + "/sandbox/operator/captures").json["captures"]) == 2
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cur,
    ):
        cur.execute(
            "SELECT status,attempt_count FROM operator_webhook_deliveries WHERE delivery_id=%s",
            (delivery,),
        )
        assert cur.fetchone() == ("delivered", 2)
        cur.execute(
            "SELECT status,attempt_count FROM operator_webhook_deliveries WHERE delivery_id=%s",
            (foreign,),
        )
        assert cur.fetchone() == ("pending", 0)
        cur.execute(
            "SELECT COUNT(*) FROM operator_events WHERE community_id=%s "
            "AND event_type='membership.mutation.prepared'",
            (COMMUNITY,),
        )
        assert cur.fetchone()[0] == 1
        cur.execute(
            "SELECT access_roles FROM community_members WHERE community_id=%s AND building_id='sonnwil-resident'",
            (COMMUNITY,),
        )
        assert cur.fetchone()[0] == []
    guide = client.get(prefix + "/sandbox/operator")
    rotated = client.post(
        prefix
        + f"/leg/community/{COMMUNITY}/operator-api/credentials/{client_id}/rotate",
        json={},
        headers={"X-CSRF-Token": _csrf(guide)},
    )
    assert rotated.status_code == 200
    assert client.get(api + "/formation", headers=headers).status_code == 401
    current = {"Authorization": "Bearer " + rotated.json["token"]}
    assert client.get(api + "/formation", headers=current).status_code == 200
    revoked = client.post(
        prefix
        + f"/leg/community/{COMMUNITY}/operator-api/credentials/{client_id}/revoke",
        json={},
        headers={"X-CSRF-Token": _csrf(guide)},
    )
    assert revoked.status_code == 200
    assert client.get(api + "/formation", headers=current).status_code == 401


def test_console_and_local_sink_recheck_role_csrf_scope_and_callback(
    village, monkeypatch
):
    """Console and sink enforce role, CSRF, community and callback boundaries."""
    client = village.test_client()
    prefix, _ = _start(client, "administrator")
    path = prefix + "/sandbox/operator"
    page = client.get(path)
    assert (
        client.post(
            path + "/dispatch", data={"mode": "deliver", "confirm": "yes"}
        ).status_code
        == 400
    )
    assert (
        client.post(
            path + "/dispatch", data={"mode": "deliver", "csrf_token": _csrf(page)}
        ).status_code
        == 400
    )
    for url in (
        "https://example.org/events",
        "https://127.0.0.1/events",
        "http://localhost/events",
        "/sandbox/sink",
        "file:///tmp/sink",
    ):
        assert _credential(client, prefix, webhook_url=url).status_code == 400
    credential = _credential(client, prefix)
    assert credential.status_code == 201
    metadata = client.get(
        prefix + f"/leg/community/{COMMUNITY}/operator-api/credentials",
        headers={"X-CSRF-Token": _csrf(page)},
    )
    assert metadata.status_code == 200
    assert "token_hash" not in metadata.text and "webhook_secret" not in metadata.text
    token = credential.json["token"]
    _switch(client, prefix, "operator")
    assert client.get(path).status_code == 403
    assert client.get(path + "/captures").status_code == 403
    profile = client.get(prefix + "/dashboard")
    assert (
        client.post(
            path + "/dispatch",
            data={"mode": "deliver", "confirm": "yes", "csrf_token": _csrf(profile)},
        ).status_code
        == 403
    )
    other = village.test_client()
    other_prefix, _ = _start(other, "administrator")
    assert other.get(path).status_code == 410
    assert other.get(other_prefix + "/sandbox/operator/captures").json["captures"] == []
    assert (
        other.get(
            other_prefix + f"/api/operator/v1/communities/{COMMUNITY}/formation",
            headers={"Authorization": "Bearer " + token},
        ).status_code
        == 401
    )
    from flask import Flask

    import sandbox_operator

    monkeypatch.delenv("OPENLEG_SANDBOX_WORKER", raising=False)
    with pytest.raises(RuntimeError, match="requires a village worker"):
        sandbox_operator.install(Flask(__name__))
