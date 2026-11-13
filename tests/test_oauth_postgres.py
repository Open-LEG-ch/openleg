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
        OAUTH_DOWNSTREAM_KEY="0123456789abcdef" * 4,  # gitleaks:allow
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
        "/oauth/authorize",
        data={
            **data,
            "csrf_token": "csrf",
            "decision": "approve",
            "capabilities": [
                c for c in data["scope"].split() if not c.startswith("community:")
            ],
        },
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
            "/oauth/authorize",
            data={**data, "decision": "approve", "capabilities": "billing.read"},
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
        "/oauth/authorize",
        data={
            **data,
            "csrf_token": "csrf",
            "decision": "approve",
            "capabilities": [
                c for c in data["scope"].split() if not c.startswith("community:")
            ],
        },
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
        "/oauth/authorize",
        data={
            **data,
            "csrf_token": "csrf",
            "decision": "approve",
            "capabilities": [
                c for c in data["scope"].split() if not c.startswith("community:")
            ],
        },
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


@pytest.mark.integration
@pytest.mark.parametrize("stage", ["exchange", "introspection", "operator"])
@pytest.mark.parametrize("change", ["role", "status", "missing"])
def test_grant_requires_current_confirmed_administrator(oauth, stage, change):
    code = grant(oauth)
    if stage != "exchange":
        token = exchange(oauth, code).json["access_token"]
        info = oauth.client.post(
            "/oauth/introspect",
            data={"token": token},
            headers={"Authorization": "Bearer " + "s" * 64},
        ).json
    with oauth.db.get_connection() as conn, conn.cursor() as cur:
        if change == "missing":
            cur.execute(
                "DELETE FROM community_members WHERE community_id='contract-leg'"
            )
        else:
            cur.execute(
                "UPDATE community_members SET "
                + change
                + "=%s WHERE community_id='contract-leg'",
                ("member" if change == "role" else "pending",),
            )
    if stage == "exchange":
        assert exchange(oauth, code).json == {"error": "invalid_grant"}
    elif stage == "introspection":
        assert oauth.client.post(
            "/oauth/introspect",
            data={"token": token},
            headers={"Authorization": "Bearer " + "s" * 64},
        ).json == {"active": False}
    else:
        assert (
            oauth.client.get(
                "/api/operator/v1/communities/contract-leg/billing/periods",
                headers={"Authorization": "Bearer " + info["downstream_token"]},
            ).status_code
            == 401
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "change", ["expires_at=NOW()-INTERVAL '1 second'", "revoked_at=NOW()"]
)
def test_downstream_requires_valid_grant(oauth, change):
    token = exchange(oauth, grant(oauth)).json["access_token"]
    info = oauth.client.post(
        "/oauth/introspect",
        data={"token": token},
        headers={"Authorization": "Bearer " + "s" * 64},
    ).json
    with oauth.db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE oauth_grants SET " + change)
    assert (
        oauth.client.get(
            "/api/operator/v1/communities/contract-leg/billing/periods",
            headers={"Authorization": "Bearer " + info["downstream_token"]},
        ).status_code
        == 401
    )


@pytest.mark.integration
def test_capability_consent_selects_community_and_subset(oauth):
    data = authorize(oauth, scope="billing.read payments.read")
    response = oauth.client.get("/oauth/authorize", query_string=data)
    assert response.status_code == 200
    assert 'name="community_id"' in response.text
    assert 'name="capabilities"' in response.text
    response = oauth.client.post(
        "/oauth/authorize",
        data={
            **data,
            "community_id": "contract-leg",
            "capabilities": "billing.read",
            "capabilities_present": "1",
            "decision": "approve",
            "csrf_token": "csrf",
        },
    )
    assert response.status_code == 302
    code = parse_qs(urlsplit(response.location).query)["code"][0]
    assert exchange(oauth, code).json["scope"] == "community:contract-leg billing.read"


@pytest.mark.integration
def test_downstream_secret_requires_random_token_material(oauth):
    import hmac

    token = exchange(oauth, grant(oauth)).json["access_token"]
    row = next(
        r
        for r in oauth.db.list_operator_api_clients("contract-leg")
        if r["actor_kind"] == "agent"
    )
    reconstructed = (
        "olk_"
        + hmac.new(
            ("k" * 64).encode(), ("oauth:" + row["id"]).encode(), hashlib.sha256
        ).hexdigest()
    )
    assert (
        oauth.client.get(
            "/api/operator/v1/communities/contract-leg/billing/periods",
            headers={"Authorization": "Bearer " + reconstructed},
        ).status_code
        == 401
    )
    assert oauth.client.post(
        "/oauth/introspect",
        data={"token": token},
        headers={"Authorization": "Bearer " + "s" * 64},
    ).json["active"]


@pytest.mark.integration
def test_sdk_discovery_authorization_and_tool(oauth, monkeypatch):
    import asyncio

    import httpx
    from mcp import ClientSession
    from mcp.client.auth import OAuthClientProvider
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata

    from mcp_sidecar.server import create_app
    from tests.test_mcp_sidecar_postgres import operator_server

    class Storage:
        tokens = None
        client_info = OAuthClientInformationFull(
            client_id="host",
            redirect_uris=["https://host.example/callback"],
            token_endpoint_auth_method="none",
        )

        async def get_tokens(self):
            return self.tokens

        async def set_tokens(self, tokens):
            self.tokens = tokens

        async def get_client_info(self):
            return self.client_info

        async def set_client_info(self, info):
            self.client_info = info

    callback = {}

    async def consent(url):
        query = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        assert "billing.read" in query["scope"].split()
        assert not any(s.startswith("community:") for s in query["scope"].split())
        assert query["code_challenge_method"] == "S256"
        page = oauth.client.get("/oauth/authorize", query_string=query)
        assert page.status_code == 200
        response = oauth.client.post(
            "/oauth/authorize",
            data={
                **query,
                "community_id": "contract-leg",
                "capabilities_present": "1",
                "capabilities": "billing.read",
                "csrf_token": "csrf",
                "decision": "approve",
            },
        )
        assert response.status_code == 302
        callback.update(parse_qs(urlsplit(response.location).query))

    async def receive_callback():
        return callback["code"][0], callback["state"][0]

    with operator_server(oauth.app) as origin:
        oauth.app.config.update(
            OAUTH_ISSUER=origin, OAUTH_RESOURCE="http://localhost/mcp"
        )
        for key, value in {
            "OPENLEG_MCP_ENABLED": "true",
            "OPENLEG_OPERATOR_ORIGIN": origin,
            "OPENLEG_OAUTH_ISSUER": origin,
            "OPENLEG_MCP_RESOURCE": "http://localhost/mcp",
            "OPENLEG_OAUTH_INTROSPECTION_SECRET": "s" * 64,
        }.items():
            monkeypatch.setenv(key, value)
        app = create_app()
        seen = []

        class Transport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                seen.append(request.url.path)
                transport = sidecar if request.url.host == "localhost" else network
                return await transport.handle_async_request(request)

        async def check():
            nonlocal sidecar, network
            sidecar = httpx.ASGITransport(app)
            network = httpx.AsyncHTTPTransport()
            storage = Storage()
            auth = OAuthClientProvider(
                "http://localhost/mcp",
                OAuthClientMetadata(
                    redirect_uris=["https://host.example/callback"],
                    token_endpoint_auth_method="none",
                ),
                storage,
                consent,
                receive_callback,
            )
            async with (
                app.router.lifespan_context(app),
                network,
                httpx.AsyncClient(transport=Transport(), auth=auth) as client,
            ):
                async with streamable_http_client(
                    "http://localhost/mcp", http_client=client
                ) as (read, write, _):
                    async with ClientSession(read, write) as sdk:
                        await sdk.initialize()
                        result = await sdk.call_tool(
                            "list_billing_periods", {"community_id": "contract-leg"}
                        )
                        assert not result.isError
            assert storage.tokens.scope == "community:contract-leg billing.read"
            for path in (
                "/.well-known/oauth-protected-resource",
                "/.well-known/oauth-authorization-server",
                "/oauth/token",
            ):
                assert path in seen

        sidecar = network = None
        asyncio.run(check())


@pytest.mark.integration
@pytest.mark.parametrize("stage", ["exchange", "introspection", "operator"])
def test_grant_fails_closed_when_community_unavailable(oauth, monkeypatch, stage):
    import formation_wizard

    code = grant(oauth)
    if stage != "exchange":
        token = exchange(oauth, code).json["access_token"]
        info = oauth.client.post(
            "/oauth/introspect",
            data={"token": token},
            headers={"Authorization": "Bearer " + "s" * 64},
        ).json

    def unavailable(*args):
        raise RuntimeError("community unavailable")

    monkeypatch.setattr(formation_wizard, "get_community_status", unavailable)
    if stage == "exchange":
        assert exchange(oauth, code).json == {"error": "invalid_grant"}
    elif stage == "introspection":
        assert oauth.client.post(
            "/oauth/introspect",
            data={"token": token},
            headers={"Authorization": "Bearer " + "s" * 64},
        ).json == {"active": False}
    else:
        assert (
            oauth.client.get(
                "/api/operator/v1/communities/contract-leg/billing/periods",
                headers={"Authorization": "Bearer " + info["downstream_token"]},
            ).status_code
            == 401
        )


@pytest.mark.integration
def test_consent_binds_selected_community(oauth):
    with oauth.db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO communities(community_id,name) VALUES ('second-leg','Second fictional LEG')"
        )
        cur.execute("""INSERT INTO community_members(community_id,building_id,role,status)
                    VALUES ('second-leg','contract-admin','admin','confirmed')""")
    data = authorize(oauth, scope="billing.read")
    page = oauth.client.get("/oauth/authorize", query_string=data)
    assert 'value="contract-leg"' in page.text
    assert 'value="second-leg"' in page.text
    response = oauth.client.post(
        "/oauth/authorize",
        data={
            **data,
            "community_id": "second-leg",
            "capabilities": "billing.read",
            "decision": "approve",
            "csrf_token": "csrf",
        },
    )
    code = parse_qs(urlsplit(response.location).query)["code"][0]
    token = exchange(oauth, code).json["access_token"]
    info = oauth.client.post(
        "/oauth/introspect",
        data={"token": token},
        headers={"Authorization": "Bearer " + "s" * 64},
    ).json
    assert info["community_id"] == "second-leg"
    assert info["scope"] == "community:second-leg billing.read"
    assert (
        oauth.client.get(
            "/api/operator/v1/communities/contract-leg/billing/periods",
            headers={"Authorization": "Bearer " + info["downstream_token"]},
        ).status_code
        == 404
    )
    assert (
        oauth.client.get(
            "/api/operator/v1/communities/second-leg/billing/periods",
            headers={"Authorization": "Bearer " + info["downstream_token"]},
        ).status_code
        == 200
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    "changes",
    [
        {"community_id": "other"},
        {"capabilities": "payments.read", "capabilities_present": "1"},
        {"capabilities_present": "1", "capabilities": []},
    ],
)
def test_consent_rejects_invalid_selection(oauth, changes):
    data = {
        **authorize(oauth, scope="billing.read"),
        "capabilities": "billing.read",
        "community_id": "contract-leg",
        "decision": "approve",
        "csrf_token": "csrf",
        **changes,
    }
    response = oauth.client.post("/oauth/authorize", data=data)
    assert response.status_code in (400, 403)
    assert "Location" not in response.headers


@pytest.mark.integration
def test_mcp_token_cannot_compute_operator_bearer(oauth):
    access = exchange(oauth, grant(oauth)).json["access_token"]
    public_derivation = (
        "olk_" + hashlib.sha256(("oauth-downstream:" + access).encode()).hexdigest()
    )
    for candidate in (access, public_derivation):
        assert (
            oauth.client.get(
                "/api/operator/v1/communities/contract-leg/billing/periods",
                headers={"Authorization": "Bearer " + candidate},
            ).status_code
            == 401
        )


@pytest.mark.integration
@pytest.mark.parametrize("check_hidden", [True, False])
def test_consent_query_cannot_restore_unchecked_capability(oauth, check_hidden):
    from html.parser import HTMLParser

    from werkzeug.datastructures import MultiDict

    class Inputs(HTMLParser):
        def __init__(self):
            super().__init__()
            self.hidden = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "input" and attrs.get("type") == "hidden":
                self.hidden.append((attrs["name"], attrs["value"]))

    query = authorize(
        oauth,
        scope="billing.read payments.read",
        capabilities="payments.read",
    )
    if check_hidden:
        query.update(
            community_id="contract-leg", capabilities_present="0", unexpected="injected"
        )
    page = oauth.client.get("/oauth/authorize", query_string=query)
    assert page.status_code == 200
    inputs = Inputs()
    inputs.feed(page.text)
    allowed = {
        "response_type",
        "client_id",
        "redirect_uri",
        "scope",
        "state",
        "code_challenge",
        "code_challenge_method",
        "resource",
        "csrf_token",
        "capabilities_present",
    }
    if check_hidden:
        assert {k for k, _ in inputs.hidden} <= allowed
    form = MultiDict(inputs.hidden)
    form.add("capabilities", "billing.read")
    form.add("community_id", "contract-leg")
    form.add("decision", "approve")
    response = oauth.client.post("/oauth/authorize", data=form, query_string=query)
    assert response.status_code == 302
    code = parse_qs(urlsplit(response.location).query)["code"][0]
    assert exchange(oauth, code).json["scope"] == "community:contract-leg billing.read"


@pytest.mark.integration
def test_consent_post_rejects_unknown_fields(oauth):
    response = oauth.client.post(
        "/oauth/authorize",
        data={
            **authorize(oauth),
            "csrf_token": "csrf",
            "decision": "approve",
            "capabilities": "billing.read",
            "unexpected": "value",
        },
    )
    assert response.status_code == 400


@pytest.mark.integration
def test_consent_requires_explicit_capability_selection(oauth):
    response = oauth.client.post(
        "/oauth/authorize",
        data={**authorize(oauth), "csrf_token": "csrf", "decision": "approve"},
    )
    assert response.status_code == 400


@pytest.mark.integration
def test_exchange_respects_the_credential_limit(oauth):
    """Each grant creates a live credential, so exchange obeys the cap of ten."""

    def active():
        with oauth.db.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS n FROM operator_api_clients "
                "WHERE community_id='contract-leg' AND active=TRUE"
            )
            return cur.fetchone()["n"]

    while active() < 10:
        assert exchange(oauth, grant(oauth)).status_code == 200
    code = grant(oauth)
    refused = exchange(oauth, code)
    assert refused.status_code == 400
    assert refused.json == {
        "error": "invalid_grant",
        "error_description": "Credential limit reached",
    }
    assert active() == 10
    # Nothing was committed: once a grant expires, the same code still works.
    with oauth.db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE oauth_grants SET expires_at=CURRENT_TIMESTAMP - INTERVAL '1 second' "
            "WHERE id=(SELECT id FROM oauth_grants ORDER BY created_at LIMIT 1)"
        )
    assert exchange(oauth, code).status_code == 200
