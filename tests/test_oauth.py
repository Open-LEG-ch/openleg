# SPDX-License-Identifier: AGPL-3.0-or-later
from flask import Flask

from operator_api import CAPABILITIES


def test_metadata():
    from oauth_server import oauth_bp

    app = Flask(__name__)
    app.config.update(
        OAUTH_ISSUER="https://auth.example", OAUTH_RESOURCE="https://mcp.example/mcp"
    )
    app.register_blueprint(oauth_bp)
    response = app.test_client().get("/.well-known/oauth-authorization-server")
    assert response.status_code == 200
    body = response.json
    assert body["issuer"] == "https://auth.example"
    assert body["authorization_endpoint"] == "https://auth.example/oauth/authorize"
    assert body["token_endpoint"] == "https://auth.example/oauth/token"
    assert body["response_types_supported"] == ["code"]
    assert body["grant_types_supported"] == ["authorization_code"]
    assert body["code_challenge_methods_supported"] == ["S256"]
    assert body["token_endpoint_auth_methods_supported"] == ["none"]


def test_sidecar_requires_oauth_and_publishes_metadata(monkeypatch):
    import asyncio

    import httpx

    from mcp_sidecar.server import create_app

    monkeypatch.setenv("OPENLEG_MCP_ENABLED", "true")
    monkeypatch.setenv("OPENLEG_OAUTH_ISSUER", "https://auth.example")
    monkeypatch.setenv("OPENLEG_MCP_RESOURCE", "https://mcp.example/mcp")
    monkeypatch.setenv("OPENLEG_OAUTH_INTROSPECTION_SECRET", "s" * 64)
    app = create_app()

    async def check():
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://localhost"
            ) as c,
        ):
            r = await c.get("/.well-known/oauth-protected-resource")
            assert r.status_code == 200
            from jsonschema import Draft202012Validator, FormatChecker

            Draft202012Validator(
                {
                    "type": "object",
                    "required": ["resource", "authorization_servers"],
                    "properties": {
                        "resource": {"type": "string", "format": "uri"},
                        "authorization_servers": {
                            "type": "array",
                            "minItems": 1,
                            "items": {"type": "string", "format": "uri"},
                        },
                        "bearer_methods_supported": {
                            "type": "array",
                            "items": {"enum": ["header", "body", "query"]},
                        },
                    },
                },
                format_checker=FormatChecker(),
            ).validate(r.json())
            assert r.json()["scopes_supported"] == sorted(CAPABILITIES)
            assert r.json()["resource"] == "https://mcp.example/mcp"
            assert r.json()["authorization_servers"] == ["https://auth.example"]
            r = await c.post("/mcp", headers={"Authorization": "Bearer olk_static"})
            assert r.status_code == 401
            assert 'scope="' in r.headers["www-authenticate"]
            assert (
                'resource_metadata="https://mcp.example/.well-known/oauth-protected-resource"'
                in r.headers["www-authenticate"]
            )

    asyncio.run(check())


def test_sidecar_rejects_invalid_identity(monkeypatch):
    import asyncio
    import time

    import httpx
    from flask import Flask, jsonify

    from mcp_sidecar.server import create_app
    from tests.test_mcp_sidecar_postgres import operator_server

    upstream = Flask(__name__)
    identity = {
        "active": True,
        "iss": "https://auth.example",
        "aud": "https://mcp.example/mcp",
        "exp": time.time() + 300,
        "scope": "community:leg billing.read",
        "community_id": "leg",
        "downstream_token": "olk_separate",
    }

    @upstream.post("/oauth/introspect")
    def introspect():
        return jsonify(identity)

    monkeypatch.setenv("OPENLEG_MCP_ENABLED", "true")
    monkeypatch.setenv("OPENLEG_OAUTH_ISSUER", "https://auth.example")
    monkeypatch.setenv("OPENLEG_MCP_RESOURCE", "https://mcp.example/mcp")
    monkeypatch.setenv("OPENLEG_OAUTH_INTROSPECTION_SECRET", "s" * 64)
    with operator_server(upstream) as origin:
        monkeypatch.setenv("OPENLEG_OPERATOR_ORIGIN", origin)
        app = create_app()

        async def check():
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app), base_url="http://localhost"
                ) as c,
            ):
                for key, value in [
                    ("iss", "https://wrong.example"),
                    ("aud", "https://wrong.example"),
                    ("exp", 0),
                    ("scope", "community:other billing.read"),
                    ("active", False),
                ]:
                    previous = identity[key]
                    identity[key] = value
                    r = await c.post(
                        "/mcp", headers={"Authorization": "Bearer ola_access"}
                    )
                    assert r.status_code == 401, key
                    identity[key] = previous

        asyncio.run(check())


def test_oauth_startup_and_metadata_shapes():
    from jsonschema import Draft202012Validator, FormatChecker

    from oauth_server import init_oauth

    app = Flask(__name__)
    app.config.update(
        OAUTH_ISSUER="https://auth.example",
        OAUTH_RESOURCE="https://mcp.example/mcp",
        OAUTH_DOWNSTREAM_KEY="0123456789abcdef" * 4,  # gitleaks:allow
        OAUTH_INTROSPECTION_SECRET="s" * 64,
        OAUTH_CLIENTS={
            "host": {"name": "Host", "redirect_uris": ["https://host.example/cb"]}
        },
    )
    init_oauth(app)
    metadata = app.test_client().get("/.well-known/oauth-authorization-server").json
    Draft202012Validator(
        {
            "type": "object",
            "required": [
                "issuer",
                "authorization_endpoint",
                "token_endpoint",
                "response_types_supported",
            ],
            "properties": {
                **{
                    k: {"type": "string", "format": "uri"}
                    for k in ("issuer", "authorization_endpoint", "token_endpoint")
                },
                **{
                    k: {
                        "type": "array",
                        "minItems": 1,
                        "uniqueItems": True,
                        "items": {"type": "string"},
                    }
                    for k in (
                        "response_types_supported",
                        "grant_types_supported",
                        "code_challenge_methods_supported",
                        "token_endpoint_auth_methods_supported",
                    )
                },
            },
        },
        format_checker=FormatChecker(),
    ).validate(metadata)


def test_oauth_secrets_redacted(caplog):
    import logging

    from mcp_sidecar.server import redact_transport_logs

    redact_transport_logs()
    # Fake values in each credential format; redaction must hide all of them.
    samples = ["ola_accesssample", "olc_codesample", "olk_downstreamsample"]
    with caplog.at_level(logging.WARNING):
        logging.getLogger("oauth.test").warning(
            "Transport failed: %s", " ".join(samples)
        )
        logging.getLogger("oauth.test").warning(
            "Authorization: Bearer introspection_secret"
        )
    assert all(sample not in caplog.text for sample in samples)
    assert "introspection_secret" not in caplog.text


def test_compose_oauth_configuration():
    from pathlib import Path

    import yaml

    config = yaml.safe_load(Path("compose.mcp.yml").read_text())["services"]
    env = config["mcp"]["environment"]
    assert env["OPENLEG_OAUTH_ISSUER"] == "http://localhost:8081"
    assert env["OPENLEG_MCP_RESOURCE"] == "http://localhost:8081/mcp"
    assert env["OPENLEG_OAUTH_INTROSPECTION_SECRET"]
    assert env["OPENLEG_OPERATOR_ORIGIN"] == "http://127.0.0.1:5000"
    assert config["mcp"]["network_mode"] == "service:flask"
    assert config["flask"]["environment"]["OPENLEG_OAUTH_CLIENTS"]
    assert config["flask"]["environment"]["OPENLEG_OAUTH_DOWNSTREAM_KEY"]
    assert "OPENLEG_OAUTH_DOWNSTREAM_KEY" not in env
    routes = Path("Caddyfile.mcp").read_text()
    for path in (
        "/.well-known/oauth-protected-resource*",
        "/.well-known/oauth-authorization-server",
        "/oauth/*",
    ):
        assert path in routes


def test_downstream_key_required_at_startup(monkeypatch):
    import pytest

    from oauth_server import init_oauth

    monkeypatch.delenv("OPENLEG_OAUTH_DOWNSTREAM_KEY", raising=False)
    for key in (None, "", "short", "k" * 64, "0" * 64, "0123456789abcdef" * 4):
        app = Flask(__name__)
        app.config.update(
            OAUTH_ISSUER="https://auth.example",
            OAUTH_RESOURCE="https://mcp.example/mcp",
            OAUTH_INTROSPECTION_SECRET="0123456789abcdef" * 4,  # gitleaks:allow
            OAUTH_CLIENTS={},
        )
        if key is not None:
            app.config["OAUTH_DOWNSTREAM_KEY"] = key
        with pytest.raises(ValueError, match="downstream key"):
            init_oauth(app)


def test_oauth_anonymous_public_and_protected_requests(monkeypatch):
    import asyncio
    import json

    import httpx
    from flask import Flask, jsonify

    from mcp_sidecar.server import create_app
    from tests.test_mcp_sidecar_postgres import operator_server

    upstream = Flask(__name__)

    @upstream.post("/oauth/introspect")
    def introspect():
        return jsonify(active=False)

    @upstream.get("/api/v1/search")
    def search():
        return jsonify({"items": ["public result"]})

    monkeypatch.setenv("OPENLEG_MCP_ENABLED", "true")
    monkeypatch.setenv("OPENLEG_OAUTH_ISSUER", "https://auth.example")
    monkeypatch.setenv("OPENLEG_MCP_RESOURCE", "https://mcp.example/mcp")
    monkeypatch.setenv("OPENLEG_OAUTH_INTROSPECTION_SECRET", "s" * 64)
    with operator_server(upstream) as origin:
        monkeypatch.setenv("OPENLEG_OPERATOR_ORIGIN", origin)
        app = create_app()

        async def check():
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app),
                    base_url="http://localhost",
                    headers={"Accept": "application/json, text/event-stream"},
                ) as client,
            ):

                def call(name):
                    return {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": name, "arguments": {"q": "Dietikon"}},
                    }

                response = await client.post("/mcp", json=call("get_search"))
                assert response.status_code == 200
                assert response.json()["result"]["structuredContent"] == {
                    "items": ["public result"]
                }
                for method in ("initialize", "tools/list", "resources/templates/list"):
                    payload = {"jsonrpc": "2.0", "id": 1, "method": method}
                    if method == "initialize":
                        payload["params"] = {
                            "protocolVersion": "2025-03-26",
                            "capabilities": {},
                            "clientInfo": {"name": "test", "version": "1"},
                        }
                    assert (await client.post("/mcp", json=payload)).status_code == 200
                challenge = None
                protected = [
                    call("list_billing_periods"),
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "resources/read",
                        "params": {"uri": "openleg://communities/leg/summary"},
                    },
                ]
                malformed_call = call("get_search")
                malformed_call["params"]["name"] = []
                for payload in [
                    malformed_call,
                    *protected,
                    [call("get_search"), protected[0]],
                ]:
                    response = await client.post("/mcp", json=payload)
                    assert response.status_code == 401
                    assert "resource_metadata=" in response.headers["www-authenticate"]
                    assert "scope=" in response.headers["www-authenticate"]
                    challenge = response.headers["www-authenticate"]
                for authorization in ("", "Bearer olk_static", "Bearer ola_invalid"):
                    response = await client.post(
                        "/mcp",
                        json=call("get_search"),
                        headers={"Authorization": authorization},
                    )
                    assert response.status_code == 401
                    assert response.headers["www-authenticate"] == challenge
                response = await client.post(
                    "/mcp", content=b"{", headers={"Content-Type": "application/json"}
                )
                assert response.status_code == 400
                assert response.json()["error"]["code"] == -32700
                response = await client.post("/mcp", content=b" " * (1024 * 1024 + 1))
                assert response.status_code == 413
                # Inspect exact raw bytes at the SDK boundary, including a batch.
                # The SDK itself currently rejects batches, after replay.
                response = await client.post(
                    "/mcp",
                    content=json.dumps([call("get_search")]).encode(),
                    headers={"Content-Type": "application/json"},
                )
                assert response.status_code != 401

        asyncio.run(check())


def test_oauth_all_operator_capabilities_and_body_replay(monkeypatch):
    import asyncio
    import time

    from flask import Flask, jsonify, request

    from mcp_sidecar.server import create_app, operations
    from tests.test_mcp_sidecar import session
    from tests.test_mcp_sidecar_postgres import operator_server

    upstream = Flask(__name__)
    identity = {
        "active": True,
        "iss": "https://auth.example",
        "aud": "https://mcp.example/mcp",
        "exp": time.time() + 300,
        "community_id": "leg",
        "scope": "community:leg unrelated.read",
        "downstream_token": "olk_separate",
    }
    calls = []

    @upstream.post("/oauth/introspect")
    def introspect():
        assert request.headers["Authorization"] == "Bearer " + "s" * 64
        assert request.form["token"] == "ola_access"
        return jsonify(identity)

    @upstream.get("/api/operator/v1/communities/leg/billing/periods")
    def periods():
        calls.append(request.headers["Authorization"])
        return jsonify(items=[], next_cursor=None)

    monkeypatch.setenv("OPENLEG_MCP_ENABLED", "true")
    monkeypatch.setenv("OPENLEG_OAUTH_ISSUER", identity["iss"])
    monkeypatch.setenv("OPENLEG_MCP_RESOURCE", identity["aud"])
    monkeypatch.setenv("OPENLEG_OAUTH_INTROSPECTION_SECRET", "s" * 64)
    with operator_server(upstream) as origin:
        monkeypatch.setenv("OPENLEG_OPERATOR_ORIGIN", origin)
        app = create_app()

        async def check():
            async with (
                app.router.lifespan_context(app),
                session(app, {"Authorization": "Bearer ola_access"}) as sdk,
            ):
                tools = {name: {} for name in operations()}
                tools.update(
                    {
                        "preview_formation_submission": {},
                        "submit_formation": {},
                        "preview_membership_mutation": {
                            "mutation_id": "m1",
                            "source_agreement_id": "agreement1",
                            "participant_id": "p1",
                            "mutation_type": "exit",
                            "effective_date": "2027-01-01",
                        },
                        "submit_membership_mutation": {
                            "mutation_id": "m1",
                            "source_agreement_id": "agreement1",
                            "participant_id": "p1",
                            "mutation_type": "exit",
                            "effective_date": "2027-01-01",
                        },
                        "respond_invoice_case": {
                            "case_id": 1,
                            "message": "Answer",
                            "status": "resolved",
                            "idempotency_key": "test-key",
                        },
                        "confirm_payment_match": {
                            "entry_id": 1,
                            "invoice_id": 1,
                            "idempotency_key": "test-key",
                        },
                    }
                )
                for name, arguments in tools.items():
                    result = await sdk.call_tool(
                        name, {"community_id": "leg", **arguments}
                    )
                    assert result.isError, name
                    assert result.content[0].text == "forbidden", (name, result)
                    assert result.structuredContent == {
                        "code": "capability_denied",
                        "field": None,
                        "retryable": False,
                    }
                    result = await sdk.call_tool(
                        name, {"community_id": "other", **arguments}
                    )
                    assert result.isError and result.content[0].text == "not_found", (
                        name,
                        result,
                    )
                assert not calls
                identity["scope"] = "community:leg billing.read"
                result = await sdk.call_tool(
                    "list_billing_periods", {"community_id": "leg"}
                )
                assert not result.isError
                assert result.structuredContent == {"items": [], "next_cursor": None}
                assert calls == ["Bearer olk_separate"]
                # Simulate loss of the endpoint identity at the SDK boundary.
                # Operator tools must still fail closed with the stable RPC error.
                import pytest
                from mcp.server.streamable_http_manager import (
                    StreamableHTTPSessionManager,
                )
                from mcp.shared.exceptions import McpError

                original = StreamableHTTPSessionManager.handle_request

                async def without_identity(self, scope, receive, send):
                    scope.pop("oauth_identity", None)
                    return await original(self, scope, receive, send)

                monkeypatch.setattr(
                    StreamableHTTPSessionManager, "handle_request", without_identity
                )
                with pytest.raises(McpError) as denied:
                    await sdk.call_tool("list_billing_periods", {"community_id": "leg"})
                assert denied.value.error.code == -32011
                assert denied.value.error.data["code"] == "authentication_required"
                assert calls == ["Bearer olk_separate"]

        asyncio.run(check())


def test_sidecar_reports_introspection_unavailable(monkeypatch):
    import asyncio

    import httpx

    from mcp_sidecar.server import create_app

    monkeypatch.setenv("OPENLEG_MCP_ENABLED", "true")
    monkeypatch.setenv("OPENLEG_OAUTH_ISSUER", "https://auth.example")
    monkeypatch.setenv("OPENLEG_MCP_RESOURCE", "https://mcp.example/mcp")
    monkeypatch.setenv("OPENLEG_OAUTH_INTROSPECTION_SECRET", "s" * 64)
    app = create_app()
    result = None

    async def upstream(self, request):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", upstream)

    async def check():
        nonlocal result
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://localhost"
            ) as client,
        ):
            for result in (
                httpx.ConnectError("offline"),
                httpx.Response(503, json={"error": "temporarily_unavailable"}),
                httpx.Response(200, text="not JSON"),
                httpx.Response(200, json=[]),
            ):
                response = await client.post(
                    "/mcp", headers={"Authorization": "Bearer ola_access"}
                )
                assert response.status_code == 503
                assert response.json() == {"error": "temporarily_unavailable"}
                assert response.headers["retry-after"] == "5"
                assert response.headers["cache-control"] == "no-store"
                assert "www-authenticate" not in response.headers

    asyncio.run(check())


def test_client_name_matches_storage_limit():
    import pytest

    from oauth_server import init_oauth

    def configured(name):
        app = Flask(__name__)
        app.config.update(
            OAUTH_ISSUER="https://auth.example",
            OAUTH_RESOURCE="https://mcp.example/mcp",
            OAUTH_DOWNSTREAM_KEY="0123456789abcdef" * 4,  # gitleaks:allow
            OAUTH_INTROSPECTION_SECRET="s" * 64,
            OAUTH_CLIENTS={
                "host": {"name": name, "redirect_uris": ["https://host.example/cb"]}
            },
        )
        return app

    init_oauth(configured("n" * 128))
    with pytest.raises(ValueError, match="client name"):
        init_oauth(configured("n" * 129))
