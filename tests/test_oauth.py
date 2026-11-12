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
        OAUTH_DOWNSTREAM_KEY="k" * 64,
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
    secrets = ["ola_accesssecret", "olc_codesecret", "olk_downstreamsecret"]
    with caplog.at_level(logging.WARNING):
        logging.getLogger("oauth.test").warning(
            "Transport failed: %s", " ".join(secrets)
        )
        logging.getLogger("oauth.test").warning(
            "Authorization: Bearer introspection_secret"
        )
    assert all(secret not in caplog.text for secret in secrets)
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
    routes = Path("Caddyfile.mcp").read_text()
    for path in (
        "/.well-known/oauth-protected-resource*",
        "/.well-known/oauth-authorization-server",
        "/oauth/*",
    ):
        assert path in routes
