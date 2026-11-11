# SPDX-License-Identifier: AGPL-3.0-or-later
"""Usage retention and hourly budgets through HTTP and PostgreSQL."""

import hashlib

import pytest
from flask import Flask

import operator_api
from tests import test_interest_postgres

interest_database = test_interest_postgres.interest_database
pytestmark = pytest.mark.integration


def test_request_prunes_only_its_expired_usage_and_preserves_budget(interest_database):
    db = interest_database
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO communities(community_id,name) VALUES (%s,'Fictional LEG')",
            ("usage-leg",),
        )
    token = "olk_usage-test"
    credential = db.create_operator_api_client(
        "usage-leg",
        "admin-1",
        "Usage test",
        ["formation.read"],
        hashlib.sha256(token.encode()).hexdigest(),
        None,
        rate_limit_per_hour=2,
    )
    other = db.create_operator_api_client(
        "usage-leg",
        "admin-1",
        "Other",
        ["formation.read"],
        "other-hash",
        None,
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        for client_id, endpoint, age in [
            (credential["id"], "expired", "2 hours"),
            (credential["id"], "recent", "10 minutes"),
            (other["id"], "other-expired", "2 hours"),
        ]:
            cur.execute(
                """INSERT INTO operator_api_usage(client_id,endpoint,called_at)
                   VALUES (%s,%s,CURRENT_TIMESTAMP - %s::interval)""",
                (client_id, endpoint, age),
            )

    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="test")
    app.register_blueprint(operator_api.operator_api_bp)
    client = app.test_client()
    url = "/api/operator/v1/communities/usage-leg/formation"
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get(url, headers=headers).status_code == 200
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT client_id,endpoint FROM operator_api_usage")
        assert {(r["client_id"], r["endpoint"]) for r in cur.fetchall()} == {
            (credential["id"], "recent"),
            (credential["id"], url),
            (other["id"], "other-expired"),
        }
        # Cleanup also commits when a full budget rejects the request.
        cur.execute(
            """INSERT INTO operator_api_usage(client_id,endpoint,called_at)
               VALUES (%s,'expired-at-limit',CURRENT_TIMESTAMP - INTERVAL '2 hours')""",
            (credential["id"],),
        )
    rejected = client.get(url, headers=headers)
    assert rejected.status_code == 429
    assert rejected.headers["Retry-After"] == "3600"
    assert rejected.get_json() == {
        "error": "Rate limit exceeded",
        "schema_version": "operator-api/1",
    }
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT endpoint FROM operator_api_usage WHERE client_id=%s",
            (credential["id"],),
        )
        assert sorted(r["endpoint"] for r in cur.fetchall()) == sorted(["recent", url])
