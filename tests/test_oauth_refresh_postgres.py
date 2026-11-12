# SPDX-License-Identifier: AGPL-3.0-or-later
import hashlib

import pytest

from tests import test_oauth_postgres

interest_database = test_oauth_postgres.interest_database
operator_http = test_oauth_postgres.operator_http
oauth = test_oauth_postgres.oauth

from tests.test_oauth_postgres import (
    exchange,
    grant,
)


def refresh(f, token, **changes):
    return f.client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": "host",
            "resource": "https://mcp.example/mcp",
            "refresh_token": token,
            **changes,
        },
    )


def introspect(f, token):
    return f.client.post(
        "/oauth/introspect",
        data={"token": token},
        headers={"Authorization": "Bearer " + "s" * 64},
    ).json


@pytest.mark.integration
def test_rotation_and_reuse(oauth):
    first = exchange(oauth, grant(oauth)).json
    downstream = introspect(oauth, first["access_token"])["downstream_token"]
    second = refresh(oauth, first["refresh_token"])
    assert second.status_code == 200
    second = second.json
    assert second["refresh_token"] != first["refresh_token"]
    assert second["access_token"] != first["access_token"]
    current = introspect(oauth, second["access_token"])
    assert current["active"] is True
    assert refresh(oauth, first["refresh_token"]).json == {"error": "invalid_grant"}
    assert introspect(oauth, second["access_token"]) == {"active": False}
    credentials = oauth.client.get(
        "/leg/community/contract-leg/operator-api/credentials",
        headers={"X-CSRF-Token": "csrf"},
    ).json["credentials"]
    assert all(not row["active"] for row in credentials if row["actor_kind"] == "agent")
    assert refresh(oauth, second["refresh_token"]).status_code == 400
    assert (
        oauth.client.get(
            "/api/operator/v1/communities/contract-leg/billing/periods",
            headers={"Authorization": "Bearer " + current["downstream_token"]},
        ).status_code
        == 401
    )
    assert (
        oauth.client.get(
            "/api/operator/v1/communities/contract-leg/billing/periods",
            headers={"Authorization": "Bearer " + downstream},
        ).status_code
        == 401
    )


@pytest.mark.integration
def test_dashboard_revoke(oauth):
    tokens = exchange(oauth, grant(oauth)).json
    info = introspect(oauth, tokens["access_token"])
    url = "/leg/community/contract-leg/operator-api/credentials"
    page = oauth.client.get(url, headers={"Accept": "text/html"})
    assert page.status_code == 200
    assert "Test host" in page.text and "billing.read" in page.text
    with oauth.db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM oauth_grants")
        gid = cur.fetchone()["id"]
    revoke = url + "/grants/" + gid + "/revoke"
    assert oauth.client.post(revoke).status_code == 400
    assert introspect(oauth, tokens["access_token"])["active"]
    assert (
        oauth.client.post(
            url + "/grants/foreign/revoke", data={"csrf_token": "csrf"}
        ).status_code
        == 404
    )
    assert oauth.client.post(revoke, data={"csrf_token": "csrf"}).status_code == 302
    assert introspect(oauth, tokens["access_token"]) == {"active": False}
    assert refresh(oauth, tokens["refresh_token"]).status_code == 400
    assert (
        oauth.client.get(
            "/api/operator/v1/communities/contract-leg/billing/periods",
            headers={"Authorization": "Bearer " + info["downstream_token"]},
        ).status_code
        == 401
    )


@pytest.mark.integration
def test_concurrent_refresh_revokes_winner(oauth):
    import time
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from types import SimpleNamespace

    tokens = exchange(oauth, grant(oauth)).json
    barrier = Barrier(2)

    def attempt():
        with oauth.app.test_client() as client:
            barrier.wait(timeout=5)
            return refresh(SimpleNamespace(client=client), tokens["refresh_token"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        with oauth.db.get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT id FROM oauth_grants FOR UPDATE")
            cur.execute(
                "SELECT id FROM operator_api_clients WHERE actor_kind='agent' FOR UPDATE"
            )
            futures = [pool.submit(attempt) for _ in range(2)]
            # Hold both rows until two distinct PostgreSQL backends are waiting.
            # Without the grant lock both requests can read the unconsumed token
            # before blocking on the credential. This makes that regression deterministic.
            deadline = time.monotonic() + 10
            while True:
                cur.execute("""SELECT count(*) AS waiting FROM pg_stat_activity
                    WHERE datname=current_database() AND wait_event_type='Lock'
                    AND pid<>pg_backend_pid()""")
                if cur.fetchone()["waiting"] == 2:
                    break
                assert time.monotonic() < deadline, (
                    "two refresh connections did not reach the locks"
                )
                time.sleep(0.01)
                cur.execute("SELECT pg_stat_clear_snapshot()")
        results = [f.result(timeout=10) for f in futures]
    assert sorted(r.status_code for r in results) == [200, 400]
    winner = next(r.json for r in results if r.status_code == 200)
    assert introspect(oauth, winner["access_token"]) == {"active": False}
    assert refresh(oauth, winner["refresh_token"]).status_code == 400


@pytest.mark.integration
@pytest.mark.parametrize(
    "change",
    [
        {"client_id": "other"},
        {"resource": "https://other.example/mcp"},
        {"scope": "community:contract-leg billing.read payments.read"},
        {"scope": "community:other billing.read"},
        {"scope": "billing.read"},
    ],
)
def test_refresh_binding(oauth, change):
    oauth.app.config["OAUTH_CLIENTS"]["other"] = oauth.app.config["OAUTH_CLIENTS"][
        "host"
    ]
    tokens = exchange(oauth, grant(oauth)).json
    assert refresh(oauth, tokens["refresh_token"], **change).status_code == 400
    assert refresh(oauth, tokens["refresh_token"]).status_code == 200


@pytest.mark.integration
def test_refresh_expiry_hashes_and_credential_revocation(oauth):
    tokens = exchange(oauth, grant(oauth)).json
    with oauth.db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT token_hash,expires_at FROM oauth_refresh_tokens")
        row = cur.fetchone()
        assert (
            row["token_hash"]
            == hashlib.sha256(tokens["refresh_token"].encode()).hexdigest()
        )
        expires = row["expires_at"]
    renewed = refresh(oauth, tokens["refresh_token"]).json
    assert introspect(oauth, renewed["access_token"])["active"]
    with oauth.db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT expires_at FROM oauth_refresh_tokens")
        assert all(r["expires_at"] == expires for r in cur.fetchall())
        cur.execute(
            "UPDATE oauth_refresh_tokens SET expires_at=NOW()-INTERVAL '1 second'"
        )
    assert refresh(oauth, renewed["refresh_token"]).status_code == 400
    fresh = exchange(oauth, grant(oauth)).json
    with oauth.db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE operator_api_clients SET active=FALSE WHERE actor_kind='agent'"
        )
    assert refresh(oauth, fresh["refresh_token"]).status_code == 400


@pytest.mark.integration
@pytest.mark.parametrize("identity", ["absent", "pending", "member"])
def test_dashboard_requires_confirmed_admin(oauth, identity):
    tokens = exchange(oauth, grant(oauth)).json
    with oauth.db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM oauth_grants")
        gid = cur.fetchone()["id"]
        if identity == "pending":
            cur.execute("UPDATE community_members SET status='pending'")
        if identity == "member":
            cur.execute(
                "UPDATE community_members SET role='member',access_roles='[]'::jsonb"
            )
    if identity == "absent":
        with oauth.client.session_transaction() as s:
            s.pop("dashboard_building_id")
    url = "/leg/community/contract-leg/operator-api/credentials"
    assert oauth.client.get(url, headers={"Accept": "text/html"}).status_code == 403
    assert (
        oauth.client.post(
            url + "/grants/" + gid + "/revoke", data={"csrf_token": "csrf"}
        ).status_code
        == 403
    )
    assert introspect(oauth, tokens["access_token"])["active"] is (identity == "absent")


@pytest.mark.integration
def test_dashboard_foreign_grant(oauth):
    tokens = exchange(oauth, grant(oauth)).json
    with oauth.db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM oauth_grants")
        gid = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO communities(community_id,name) VALUES ('other','Other')"
        )
        cur.execute(
            "INSERT INTO community_members(community_id,building_id,role,status) VALUES ('other','contract-admin','admin','confirmed')"
        )
    assert (
        oauth.client.post(
            "/leg/community/other/operator-api/credentials/grants/" + gid + "/revoke",
            data={"csrf_token": "csrf"},
        ).status_code
        == 404
    )
    assert introspect(oauth, tokens["access_token"])["active"]
    page = oauth.client.get(
        "/leg/community/other/operator-api/credentials", headers={"Accept": "text/html"}
    )
    assert page.status_code == 200
    assert "Test host" not in page.text


@pytest.mark.integration
@pytest.mark.parametrize("revoke_method", ["dashboard", "reuse"])
def test_next_mcp_request_rejected(oauth, monkeypatch, revoke_method):
    import asyncio

    import httpx

    from mcp_sidecar.server import create_app
    from tests.test_mcp_sidecar import session
    from tests.test_mcp_sidecar_postgres import operator_server

    first = exchange(oauth, grant(oauth)).json
    tokens = refresh(oauth, first["refresh_token"]).json
    monkeypatch.setenv("OPENLEG_MCP_ENABLED", "true")
    monkeypatch.setenv("OPENLEG_OAUTH_ISSUER", "https://auth.example")
    monkeypatch.setenv("OPENLEG_MCP_RESOURCE", "https://mcp.example/mcp")
    monkeypatch.setenv("OPENLEG_OAUTH_INTROSPECTION_SECRET", "s" * 64)
    with operator_server(oauth.app) as origin:
        monkeypatch.setenv("OPENLEG_OPERATOR_ORIGIN", origin)
        app = create_app()

        async def check():
            async with app.router.lifespan_context(app):
                with pytest.raises(BaseExceptionGroup) as rejected:
                    async with session(
                        app, {"Authorization": "Bearer " + tokens["access_token"]}
                    ) as sdk:
                        result = await sdk.call_tool(
                            "list_billing_periods", {"community_id": "contract-leg"}
                        )
                        assert not result.isError
                        if revoke_method == "reuse":
                            assert (
                                refresh(oauth, first["refresh_token"]).status_code
                                == 400
                            )
                        else:
                            with (
                                oauth.db.get_connection() as conn,
                                conn.cursor() as cur,
                            ):
                                cur.execute("SELECT id FROM oauth_grants")
                                gid = cur.fetchone()["id"]
                            assert (
                                oauth.client.post(
                                    "/leg/community/contract-leg/operator-api/credentials/grants/"
                                    + gid
                                    + "/revoke",
                                    data={"csrf_token": "csrf"},
                                ).status_code
                                == 302
                            )
                        await sdk.call_tool(
                            "list_billing_periods", {"community_id": "contract-leg"}
                        )

                def leaves(exc):
                    if isinstance(exc, BaseExceptionGroup):
                        return [e for nested in exc.exceptions for e in leaves(nested)]
                    return [exc]

                errors = leaves(rejected.value)
                assert any(
                    isinstance(e, httpx.HTTPStatusError)
                    and e.response.status_code == 401
                    for e in errors
                )
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://localhost:8000",
                ) as client:
                    denied = await client.post(
                        "/mcp",
                        headers={"Authorization": "Bearer " + tokens["access_token"]},
                    )
                    assert denied.status_code == 401

        asyncio.run(check())
    assert len(oauth.operator_requests) == 1
    assert refresh(oauth, tokens["refresh_token"]).status_code == 400


@pytest.mark.integration
def test_french_grants(oauth):
    exchange(oauth, grant(oauth))
    page = oauth.client.get(
        "/leg/community/contract-leg/operator-api/credentials?lang=fr",
        headers={"Accept": "text/html"},
    )
    assert page.status_code == 200
    assert "Autorisations OAuth" in page.text
    assert "Révoquer l’accès de l’agent" in page.text


@pytest.mark.integration
@pytest.mark.parametrize(
    "guard",
    ["grant_revoked", "credential_rotated", "client_removed", "resource_changed"],
)
def test_refresh_rechecks_grant_and_configuration(oauth, guard):
    tokens = exchange(oauth, grant(oauth)).json
    with oauth.db.get_connection() as conn, conn.cursor() as cur:
        if guard == "grant_revoked":
            cur.execute("UPDATE oauth_grants SET revoked_at=NOW()")
        elif guard == "credential_rotated":
            cur.execute(
                "UPDATE operator_api_clients SET token_hash='rotated' WHERE actor_kind='agent'"
            )
    changes = {}
    if guard == "client_removed":
        oauth.app.config["OAUTH_CLIENTS"] = {}
    if guard == "resource_changed":
        oauth.app.config["OAUTH_RESOURCE"] = "https://changed.example/mcp"
        changes["resource"] = "https://changed.example/mcp"
    assert refresh(oauth, tokens["refresh_token"], **changes).status_code == 400


@pytest.mark.integration
def test_dashboard_projection_and_last_use(oauth):
    tokens = exchange(oauth, grant(oauth)).json
    with oauth.db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT last_used_at FROM oauth_grants")
        assert cur.fetchone()["last_used_at"] is None
    info = introspect(oauth, tokens["access_token"])
    with oauth.db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT last_used_at FROM oauth_grants")
        assert cur.fetchone()["last_used_at"] is not None
    page = oauth.client.get(
        "/leg/community/contract-leg/operator-api/credentials",
        headers={"Accept": "text/html"},
    )
    for secret in [
        tokens["access_token"],
        tokens["refresh_token"],
        info["downstream_token"],
    ]:
        assert secret not in page.text
        assert hashlib.sha256(secret.encode()).hexdigest() not in page.text


@pytest.mark.integration
def test_reuse_only_revokes_its_grant(oauth):
    compromised = exchange(oauth, grant(oauth)).json
    independent = exchange(oauth, grant(oauth)).json
    assert refresh(oauth, compromised["refresh_token"]).status_code == 200
    assert refresh(oauth, compromised["refresh_token"]).status_code == 400
    assert introspect(oauth, independent["access_token"])["active"]
    assert refresh(oauth, independent["refresh_token"]).status_code == 200


@pytest.mark.integration
def test_refresh_replaces_downstream_secret(oauth):
    first = exchange(oauth, grant(oauth)).json
    old = introspect(oauth, first["access_token"])["downstream_token"]
    second = refresh(oauth, first["refresh_token"])
    assert second.status_code == 200
    new = introspect(oauth, second.json["access_token"])["downstream_token"]
    assert new != old
    assert introspect(oauth, first["access_token"]) == {"active": False}
    for secret, status in [(old, 401), (new, 200)]:
        assert (
            oauth.client.get(
                "/api/operator/v1/communities/contract-leg/billing/periods",
                headers={"Authorization": "Bearer " + secret},
            ).status_code
            == status
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalidity", ["role", "status", "removed", "unavailable", "expired"]
)
def test_refresh_requires_valid_grant(oauth, monkeypatch, invalidity):
    import formation_wizard

    tokens = exchange(oauth, grant(oauth)).json
    with oauth.db.get_connection() as conn, conn.cursor() as cur:
        if invalidity == "role":
            cur.execute(
                "UPDATE community_members SET role='member',access_roles='[]'::jsonb"
            )
        elif invalidity == "status":
            cur.execute("UPDATE community_members SET status='pending'")
        elif invalidity == "removed":
            cur.execute("DELETE FROM community_members")
        elif invalidity == "expired":
            cur.execute("UPDATE oauth_grants SET expires_at=NOW()-INTERVAL '1 second'")
    if invalidity == "unavailable":

        def unavailable(*args):
            raise RuntimeError("community unavailable")

        monkeypatch.setattr(formation_wizard, "get_community_status", unavailable)
    assert refresh(oauth, tokens["refresh_token"]).json == {"error": "invalid_grant"}


@pytest.mark.integration
def test_legacy_grant_requires_new_consent(oauth):
    tokens = exchange(oauth, grant(oauth)).json
    with oauth.db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE oauth_grants SET downstream_hash=NULL")
    assert refresh(oauth, tokens["refresh_token"]).json == {"error": "invalid_grant"}
