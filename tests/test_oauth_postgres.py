# SPDX-License-Identifier: AGPL-3.0-or-later
import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import pytest

from tests import operator_contract_helpers

interest_database = operator_contract_helpers.interest_database
operator_http = operator_contract_helpers.operator_http


@pytest.fixture
def oauth(operator_http):
    from oauth_server import oauth_bp

    app = operator_http.app
    app.secret_key = "test"
    from pathlib import Path

    import i18n

    app.template_folder = str(Path(__file__).resolve().parents[1] / "templates")
    app.config["BABEL_TRANSLATION_DIRECTORIES"] = str(
        Path(__file__).resolve().parents[1] / "translations"
    )
    i18n.init_app(app)
    app.jinja_env.globals["public_site_url"] = lambda path: "/" + path
    app.config.update(
        OAUTH_ISSUER="https://auth.example",
        OAUTH_RESOURCE="https://mcp.example/mcp",
        OAUTH_DOWNSTREAM_KEY="k" * 64,
        OAUTH_INTROSPECTION_SECRET="s" * 64,
        OAUTH_CLIENTS={
            "host": {
                "name": "Test host",
                "redirect_uris": ["https://host.example/callback"],
            }
        },
    )
    app.register_blueprint(oauth_bp)
    from flask import request

    operator_http.operator_requests = []

    @app.before_request
    def record_calls():
        if request.path.startswith("/api/operator/"):
            operator_http.operator_requests.append(request.path)

    with operator_http.client.session_transaction() as s:
        s["dashboard_building_id"] = "contract-admin"
        s["dashboard_csrf_token"] = "csrf"
    return operator_http


def authorize(f, **changes):
    data = {
        "client_id": "host",
        "redirect_uri": "https://host.example/callback",
        "response_type": "code",
        "scope": "community:contract-leg billing.read",
        "resource": "https://mcp.example/mcp",
        "state": "state",
        "code_challenge": base64.urlsafe_b64encode(
            hashlib.sha256(("a" * 43).encode()).digest()
        )
        .decode()
        .rstrip("="),
        "code_challenge_method": "S256",
    }
    data.update(changes)
    return data


def grant(f):
    data = authorize(f)
    consent = f.client.get("/oauth/authorize", query_string=data)
    assert consent.status_code == 200
    assert "Abrechnungen lesen" in consent.text
    response = f.client.post(
        "/oauth/authorize", data={**data, "csrf_token": "csrf", "decision": "approve"}
    )
    assert response.status_code == 302
    query = parse_qs(urlsplit(response.location).query)
    assert query["state"] == ["state"]
    return query["code"][0]


def exchange(f, code, **changes):
    data = {
        "grant_type": "authorization_code",
        "client_id": "host",
        "code": code,
        "redirect_uri": "https://host.example/callback",
        "code_verifier": "a" * 43,
        "resource": "https://mcp.example/mcp",
    }
    data.update(changes)
    return f.client.post("/oauth/token", data=data)


@pytest.mark.integration
def test_flow(oauth):
    code = grant(oauth)
    response = exchange(oauth, code)
    assert response.status_code == 200
    token = response.json["access_token"]
    assert response.json["expires_in"] == 300
    result = oauth.client.post(
        "/oauth/introspect",
        data={"token": token},
        headers={"Authorization": "Bearer " + "s" * 64},
    )
    assert result.status_code == 200
    info = result.json
    assert info["active"] is True
    assert info["scope"] == "community:contract-leg billing.read"
    assert info["aud"] == "https://mcp.example/mcp"
    assert info["iss"] == "https://auth.example"
    assert info["downstream_token"] != token
    downstream = oauth.client.get(
        "/api/operator/v1/communities/contract-leg/billing/periods",
        headers={"Authorization": "Bearer " + info["downstream_token"]},
    )
    assert downstream.status_code == 200
    rows = oauth.db.list_operator_api_clients("contract-leg")
    assert any(
        r["actor_kind"] == "agent" and r["capabilities"] == ["billing.read"]
        for r in rows
    )
    assert exchange(oauth, code).status_code == 400


@pytest.mark.integration
@pytest.mark.parametrize(
    "change",
    [
        {"redirect_uri": "https://evil.example/callback"},
        {"code_verifier": "b" * 43},
        {"resource": "https://other.example/mcp"},
        {"client_id": "unknown"},
    ],
)
def test_exchange_rejects_mismatch(oauth, change):
    code = grant(oauth)
    assert exchange(oauth, code, **change).status_code == 400


@pytest.mark.integration
@pytest.mark.parametrize(
    "change",
    [
        {"redirect_uri": "https://host.example/callback/"},
        {"scope": "community:contract-leg community:other billing.read"},
        {"scope": "community:contract-leg credential.create"},
        {"code_challenge_method": "plain"},
        {"resource": "https://other.example/mcp"},
    ],
)
def test_authorization_rejects_invalid_request(oauth, change):
    r = oauth.client.get("/oauth/authorize", query_string=authorize(oauth, **change))
    assert r.status_code == 400
    assert "Location" not in r.headers


@pytest.mark.integration
def test_admin_and_csrf_required(oauth):
    data = authorize(oauth)
    assert (
        oauth.client.post(
            "/oauth/authorize", data={**data, "decision": "approve"}
        ).status_code
        == 403
    )
    with oauth.db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE community_members SET status='pending' WHERE community_id='contract-leg'"
        )
    assert oauth.client.get("/oauth/authorize", query_string=data).status_code == 403


@pytest.mark.integration
def test_expiry_and_revocation(oauth):
    token = exchange(oauth, grant(oauth)).json["access_token"]

    def introspect(secret="s" * 64):
        return oauth.client.post(
            "/oauth/introspect",
            data={"token": token},
            headers={"Authorization": "Bearer " + secret},
        )

    assert introspect("wrong").status_code == 401
    with oauth.db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE oauth_grants SET expires_at=NOW()-INTERVAL '1 second'")
    assert introspect().json == {"active": False}


@pytest.mark.integration
def test_mcp_host_flow(oauth, monkeypatch):
    import asyncio

    from flask import request

    from mcp_sidecar.server import create_app
    from tests.test_mcp_sidecar import session
    from tests.test_mcp_sidecar_postgres import operator_server

    seen = []

    @oauth.app.before_request
    def record_operator_header():
        if request.path.startswith("/api/operator/"):
            seen.append(request.headers.get("Authorization"))

    access = exchange(oauth, grant(oauth)).json["access_token"]
    monkeypatch.setenv("OPENLEG_MCP_ENABLED", "true")
    monkeypatch.setenv("OPENLEG_OAUTH_ISSUER", "https://auth.example")
    monkeypatch.setenv("OPENLEG_MCP_RESOURCE", "https://mcp.example/mcp")
    monkeypatch.setenv("OPENLEG_OAUTH_INTROSPECTION_SECRET", "s" * 64)
    with operator_server(oauth.app) as origin:
        monkeypatch.setenv("OPENLEG_OPERATOR_ORIGIN", origin)
        app = create_app()

        async def check():
            async with (
                app.router.lifespan_context(app),
                session(app, {"Authorization": "Bearer " + access}) as sdk,
            ):
                result = await sdk.call_tool(
                    "list_billing_periods", {"community_id": "contract-leg"}
                )
                assert not result.isError
                denied = await sdk.call_tool(
                    "list_billing_periods", {"community_id": "other"}
                )
                assert denied.isError and denied.content[0].text == "not_found"

        asyncio.run(check())
        assert len(seen) == 1
        assert seen and all(h.startswith("Bearer olk_") for h in seen)
        assert all(access not in h for h in seen)


@pytest.mark.integration
def test_scope_cannot_expand_at_exchange(oauth):
    data = authorize(oauth, scope="community:contract-leg payments.read")
    r = oauth.client.post(
        "/oauth/authorize", data={**data, "csrf_token": "csrf", "decision": "approve"}
    )
    code = parse_qs(urlsplit(r.location).query)["code"][0]
    token = exchange(oauth, code, scope="community:contract-leg billing.read").json[
        "access_token"
    ]
    info = oauth.client.post(
        "/oauth/introspect",
        data={"token": token},
        headers={"Authorization": "Bearer " + "s" * 64},
    ).json
    assert info["scope"] == "community:contract-leg payments.read"
    assert (
        oauth.client.get(
            "/api/operator/v1/communities/contract-leg/billing/periods",
            headers={"Authorization": "Bearer " + info["downstream_token"]},
        ).status_code
        == 403
    )
    with oauth.db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE operator_api_clients SET active=FALSE WHERE actor_kind='agent'"
        )
    assert oauth.client.post(
        "/oauth/introspect",
        data={"token": token},
        headers={"Authorization": "Bearer " + "s" * 64},
    ).json == {"active": False}


@pytest.mark.integration
def test_expired_code_and_foreign_administrator(oauth):
    code = grant(oauth)
    with oauth.db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE oauth_codes SET expires_at=NOW()-INTERVAL '1 second'")
    assert exchange(oauth, code).status_code == 400
    r = oauth.client.get(
        "/oauth/authorize",
        query_string=authorize(oauth, scope="community:other billing.read"),
    )
    assert r.status_code == 403


@pytest.mark.integration
def test_sidecar_enforces_capability(oauth, monkeypatch):
    import asyncio

    from mcp_sidecar.server import create_app
    from tests.test_mcp_sidecar import session
    from tests.test_mcp_sidecar_postgres import operator_server

    data = authorize(oauth, scope="community:contract-leg payments.read")
    r = oauth.client.post(
        "/oauth/authorize", data={**data, "csrf_token": "csrf", "decision": "approve"}
    )
    code = parse_qs(urlsplit(r.location).query)["code"][0]
    access = exchange(oauth, code).json["access_token"]
    monkeypatch.setenv("OPENLEG_MCP_ENABLED", "true")
    monkeypatch.setenv("OPENLEG_OAUTH_ISSUER", "https://auth.example")
    monkeypatch.setenv("OPENLEG_MCP_RESOURCE", "https://mcp.example/mcp")
    monkeypatch.setenv("OPENLEG_OAUTH_INTROSPECTION_SECRET", "s" * 64)
    with operator_server(oauth.app) as origin:
        monkeypatch.setenv("OPENLEG_OPERATOR_ORIGIN", origin)
        app = create_app()

        async def check():
            async with (
                app.router.lifespan_context(app),
                session(app, {"Authorization": "Bearer " + access}) as sdk,
            ):
                denied = await sdk.call_tool(
                    "list_billing_periods", {"community_id": "contract-leg"}
                )
                assert denied.isError and denied.content[0].text == "forbidden"

        asyncio.run(check())
        assert oauth.operator_requests == []
        # The sidecar must reject before making any downstream operator call.
        assert (
            oauth.db.get_operator_api_usage_count(
                next(
                    r["id"]
                    for r in oauth.db.list_operator_api_clients("contract-leg")
                    if r["actor_kind"] == "agent"
                )
            )
            == 0
        )


@pytest.mark.integration
def test_french_consent(oauth):
    with oauth.client.session_transaction() as s:
        s["locale"] = "fr_CH"
    response = oauth.client.get(
        "/oauth/authorize", query_string={**authorize(oauth), "lang": "fr"}
    )
    assert response.status_code == 200
    assert "Consulter les décomptes" in response.text
    assert "billing.read" in response.text


@pytest.mark.integration
def test_code_bound_to_registered_client_and_redirect(oauth):
    other_uri = "https://host.example/second"
    oauth.app.config["OAUTH_CLIENTS"]["host"]["redirect_uris"].append(other_uri)
    oauth.app.config["OAUTH_CLIENTS"]["second"] = {
        "name": "Second host",
        "redirect_uris": ["https://host.example/callback"],
    }
    code = grant(oauth)
    assert exchange(oauth, code, redirect_uri=other_uri).status_code == 400
    assert exchange(oauth, code, client_id="second").status_code == 400
    assert exchange(oauth, code).status_code == 200
