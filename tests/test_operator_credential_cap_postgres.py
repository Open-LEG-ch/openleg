# SPDX-License-Identifier: AGPL-3.0-or-later
"""Active credential limits through HTTP and PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

import pytest

from tests import test_interest_postgres
from tests.test_operator_credential_cap import (
    ADMIN_STATUS,
    ERROR,
    HEADERS,
    PAYLOAD,
    URL,
    admin_client,
    credential_app,
)

interest_database = test_interest_postgres.interest_database
pytestmark = pytest.mark.integration


@pytest.fixture
def credential_http(interest_database):
    with interest_database.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO communities(community_id,name) VALUES (%s,'Fictional LEG')",
            ("cap-leg",),
        )
    app = credential_app()
    with patch(
        "operator_api.formation_wizard.get_community_status", return_value=ADMIN_STATUS
    ):
        yield app


def create(client):
    return client.post(URL, json=PAYLOAD, headers=HEADERS)


def test_tenth_succeeds_eleventh_writes_nothing_and_revocation_frees_slot(
    credential_http, interest_database
):
    client = admin_client(credential_http)
    with interest_database.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO communities(community_id,name) VALUES (%s,'Fictional LEG')",
            ("other-leg",),
        )
    for _ in range(10):
        assert (
            client.post(
                URL.replace("cap-leg", "other-leg"), json=PAYLOAD, headers=HEADERS
            ).status_code
            == 201
        )
    responses = [create(client) for _ in range(10)]
    assert [response.status_code for response in responses] == [201] * 10
    before = interest_database.list_operator_api_clients("cap-leg")
    rejected = create(client)
    assert rejected.status_code == 409
    assert rejected.get_json() == ERROR
    assert interest_database.list_operator_api_clients("cap-leg") == before

    credential_id = responses[0].get_json()["credential"]["id"]
    assert (
        client.post(f"{URL}/{credential_id}/revoke", headers=HEADERS).status_code == 200
    )
    assert create(client).status_code == 201
    rows = interest_database.list_operator_api_clients("cap-leg")
    assert len(rows) == 11
    assert sum(row["active"] for row in rows) == 10


def test_concurrent_creations_share_the_last_slot(credential_http, interest_database):
    client = admin_client(credential_http)
    assert [create(client).status_code for _ in range(9)] == [201] * 9
    # Widen the insert overlap so an unlocked count/insert reliably oversubscribes.
    with interest_database.get_connection() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE FUNCTION delay_credential_insert() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                PERFORM pg_sleep(0.3);
                RETURN NEW;
            END;
            $$;
            CREATE TRIGGER delay_credential_insert BEFORE INSERT ON operator_api_clients
            FOR EACH ROW EXECUTE FUNCTION delay_credential_insert();
        """)
    ready = Barrier(2)

    def request():
        own_client = admin_client(credential_http)
        ready.wait(timeout=5)
        return create(own_client)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(request) for _ in range(2)]
        responses = [future.result(timeout=10) for future in futures]
    assert sorted(response.status_code for response in responses) == [201, 409]
    assert next(r for r in responses if r.status_code == 409).get_json() == ERROR
    assert len(interest_database.list_operator_api_clients("cap-leg")) == 10


def test_rotation_cannot_reactivate_past_the_limit(credential_http, interest_database):
    """Rotating a revoked credential reactivates it, which needs a free slot."""
    client = admin_client(credential_http)
    ids = [create(client).get_json()["credential"]["id"] for _ in range(10)]
    revoked = ids[0]
    assert client.post(f"{URL}/{revoked}/revoke", headers=HEADERS).status_code == 200
    assert create(client).status_code == 201
    before = interest_database.list_operator_api_clients("cap-leg")

    rejected = client.post(f"{URL}/{revoked}/rotate", headers=HEADERS)
    assert rejected.status_code == 409
    assert rejected.get_json() == ERROR
    assert interest_database.list_operator_api_clients("cap-leg") == before

    # An active credential still rotates at the limit.
    assert client.post(f"{URL}/{ids[1]}/rotate", headers=HEADERS).status_code == 200
    # With a free slot, rotation reactivates the revoked credential.
    assert client.post(f"{URL}/{ids[2]}/revoke", headers=HEADERS).status_code == 200
    assert client.post(f"{URL}/{revoked}/rotate", headers=HEADERS).status_code == 200
    rows = interest_database.list_operator_api_clients("cap-leg")
    assert sum(row["active"] for row in rows) == 10


@pytest.mark.parametrize("expired", [True, False])
def test_expired_oauth_grants_free_their_slots(
    credential_http, interest_database, expired
):
    """An OAuth grant's downstream credential counts only while the grant lives."""
    client = admin_client(credential_http)
    assert create(client).status_code == 201
    for index in range(9):
        row = interest_database.create_operator_api_client(
            "cap-leg",
            "building-1",
            f"Agent {index}",
            ["formation.read"],
            f"grant-hash-{index}",
            None,
            actor_kind="agent",
            agent_label=f"Agent {index}",
        )
        with interest_database.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO oauth_grants(id,token_hash,payload,expires_at) "
                "VALUES (%s,%s,'{}', CURRENT_TIMESTAMP + %s * INTERVAL '1 hour')",
                (row["id"], f"access-hash-{index}", -1 if expired else 1),
            )
            cur.execute(
                "INSERT INTO oauth_refresh_tokens(token_hash,grant_id,expires_at) "
                "VALUES (%s,%s,CURRENT_TIMESTAMP + %s * INTERVAL '1 hour')",
                (f"refresh-hash-{index}", row["id"], -1 if expired else 1),
            )
    response = create(client)
    assert response.status_code == (201 if expired else 409)
