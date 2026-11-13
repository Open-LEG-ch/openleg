# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
import json
from pathlib import Path

import pytest

from tests.test_mcp_sidecar import session

PUBLIC_NAMES = {
    "get_municipalities",
    "get_municipalities_bfs",
    "get_municipalities_bfs_tariffs",
    "get_municipalities_bfs_solar",
    "get_tariffs",
    "get_search",
    "get_rankings",
    "get_site_rankings",
    "get_site_rankings_movers",
}


@pytest.fixture
def public_app(monkeypatch):
    from unittest.mock import MagicMock

    from flask import Flask

    import api_public
    from tests.conftest import (
        MOCK_ELCOM_TARIFFS,
        MOCK_MUNICIPALITY_PROFILE,
        MOCK_PROFILES_LIST,
        MOCK_SONNENDACH,
    )

    db = MagicMock()
    db.get_all_municipality_profiles.return_value = MOCK_PROFILES_LIST
    db.get_municipality_profile.return_value = MOCK_MUNICIPALITY_PROFILE
    db.get_elcom_tariffs.return_value = MOCK_ELCOM_TARIFFS
    db.get_all_elcom_tariffs.return_value = MOCK_ELCOM_TARIFFS
    db.get_sonnendach_municipal.return_value = MOCK_SONNENDACH
    monkeypatch.setattr(api_public, "db", db)
    app = Flask(__name__)
    app.register_blueprint(api_public.public_api_bp)
    return app


def test_anonymous_catalog_matches_contract(monkeypatch):
    from mcp_sidecar.server import create_app

    monkeypatch.setenv("OPENLEG_MCP_ENABLED", "true")
    app = create_app()
    spec = json.loads(Path("docs/openapi/public-v1.json").read_text())

    async def check():
        async with app.router.lifespan_context(app), session(app) as sdk:
            tools = {t.name: t for t in (await sdk.list_tools()).tools}
            # Public tools carry no capability metadata; operator tools do.
            assert {name for name, t in tools.items() if not t.meta} == PUBLIC_NAMES
            assert "list_billing_periods" in tools
            for operations in spec["paths"].values():
                op = operations.get("get", {})
                if op.get("operationId") not in PUBLIC_NAMES:
                    continue
                tool = tools[op["operationId"]]
                params = op.get("parameters", [])
                assert tool.inputSchema == {
                    "type": "object",
                    "properties": {p["name"]: p["schema"] for p in params},
                    "required": [p["name"] for p in params if p.get("required")],
                    "additionalProperties": False,
                }
                assert tool.annotations.readOnlyHint is True
            from mcp.shared.exceptions import McpError

            # Operator tools still need a bearer: an anonymous call is an MCP
            # authorization error, not a tool result.
            with pytest.raises(McpError) as denied:
                await sdk.call_tool("list_billing_periods", {"community_id": "one"})
            assert denied.value.error.code == -32011
            assert denied.value.error.data["code"] == "authentication_required"

    asyncio.run(check())


def test_public_calls_real_routes_without_credentials(public_app, monkeypatch):
    app = public_app
    from decimal import Decimal
    from unittest.mock import patch

    from flask import request
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    from mcp_sidecar.server import create_app
    from tests.test_mcp_sidecar_postgres import operator_server

    seen = []

    @app.before_request
    def capture():
        seen.append((request.path, request.headers.get("Authorization")))

    spec = json.loads(Path("docs/openapi/public-v1.json").read_text())
    registry = Registry().with_resource(
        "urn:public",
        Resource.from_contents(
            {"$schema": "https://json-schema.org/draft/2020-12/schema", **spec}
        ),
    )
    rows = [
        {
            "bfs_number": 261,
            "name": "Dietikon",
            "kanton": "ZH",
            "population": 1000,
            "pv_score_pct": Decimal("77.00"),
            "pv_untapped_kw": Decimal("230.50"),
            "year": 2025,
            "score_now": Decimal("77.0000"),
            "score_prev": Decimal("78.2500"),
            "delta": Decimal("-1.2500"),
        }
    ]
    monkeypatch.setenv("OPENLEG_MCP_ENABLED", "true")
    # A real proxy setting must not affect the fixed upstream.
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    with (
        operator_server(app) as origin,
        patch("store.ranking.get_pv_profiles", return_value=rows),
        patch("store.ranking.get_pv_movers", return_value=rows),
    ):
        monkeypatch.setenv("OPENLEG_OPERATOR_ORIGIN", origin)
        sidecar = create_app()

        async def check():
            async with sidecar.router.lifespan_context(sidecar):
                for headers in (None, {"Authorization": "Bearer olk_do_not_forward"}):
                    async with session(sidecar, headers) as sdk:
                        for path, methods in spec["paths"].items():
                            op = methods.get("get", {})
                            if op.get("operationId") not in PUBLIC_NAMES:
                                continue
                            args = {"bfs": 261} if "{bfs}" in path else {}
                            if path == "/search":
                                args = {"q": "Diet", "limit": 1}
                            if path.endswith("/tariffs"):
                                args["year"] = 2026
                            result = await sdk.call_tool(op["operationId"], args)
                            assert not result.isError, result
                            actual = result.structuredContent
                            expected = (
                                app.test_client()
                                .get(
                                    "/api/v1" + path.format(bfs=261),
                                    query_string={
                                        k: v for k, v in args.items() if k != "bfs"
                                    },
                                )
                                .get_json()
                            )
                            assert actual == expected
                            pointer = path.replace("~", "~0").replace("/", "~1")
                            Draft202012Validator(
                                {
                                    "$ref": f"urn:public#/paths/{pointer}/get/responses/200/content/application~1json/schema"
                                },
                                registry=registry,
                            ).validate(actual)
                            if path == "/site/rankings":
                                assert actual["rankings"][0]["pv_score_pct"] == "77.00"
                assert all(auth is None for _, auth in seen)
                async with session(sidecar) as sdk:
                    count = len(seen)
                    for name, args in (
                        ("get_municipalities_bfs", {"bfs": "../admin"}),
                        ("get_search", {"q": "Diet", "url": "http://evil.test"}),
                        ("post_leg_value_gap", {}),
                        ("get_address_profile", {}),
                    ):
                        result = await sdk.call_tool(name, args)
                        assert result.isError
                        assert result.content[0].text == "invalid_arguments"
                    assert len(seen) == count

        asyncio.run(check())


def test_public_rate_limit_is_enforced_by_flask(public_app, monkeypatch):
    app = public_app
    from flask import jsonify

    from mcp_sidecar.server import create_app
    from security_extensions import limiter
    from tests.test_mcp_sidecar_postgres import operator_server

    app.config.update(RATELIMIT_STORAGE_URI="memory://", RATELIMIT_ENABLED=True)
    limiter.init_app(app)

    @app.errorhandler(429)
    def limited(error):
        return jsonify(error="private upstream diagnostic"), 429, {"Retry-After": "60"}

    with app.app_context():
        limiter.reset()
    client = app.test_client()
    for _ in range(240):
        assert client.get("/api/v1/municipalities").status_code == 200
    monkeypatch.setenv("OPENLEG_MCP_ENABLED", "true")
    with operator_server(app) as origin:
        monkeypatch.setenv("OPENLEG_OPERATOR_ORIGIN", origin)
        sidecar = create_app()

        async def check():
            async with (
                sidecar.router.lifespan_context(sidecar),
                session(sidecar) as sdk,
            ):
                result = await sdk.call_tool("get_municipalities", {})
                assert result.isError
                assert result.content[0].text == "rate_limited"
                assert result.structuredContent == {
                    "code": "rate_limited",
                    "field": None,
                    "retryable": True,
                    "retry_after": 60,
                }
                assert "private upstream diagnostic" not in str(result)

        asyncio.run(check())


def test_public_redirect_is_not_followed(public_app, monkeypatch):
    app = public_app
    from flask import redirect, request

    from mcp_sidecar.server import create_app
    from tests.test_mcp_sidecar_postgres import operator_server

    seen = []

    @app.before_request
    def redirect_public():
        seen.append(request.path)
        return redirect("/private-destination")

    monkeypatch.setenv("OPENLEG_MCP_ENABLED", "true")
    with operator_server(app) as origin:
        monkeypatch.setenv("OPENLEG_OPERATOR_ORIGIN", origin)
        sidecar = create_app()

        async def check():
            async with (
                sidecar.router.lifespan_context(sidecar),
                session(sidecar) as sdk,
            ):
                result = await sdk.call_tool("get_municipalities", {})
                assert result.isError
                assert result.content[0].text == "public_error"
                assert seen == ["/api/v1/municipalities"]

        asyncio.run(check())


def test_integral_floats_keep_their_integer_meaning(public_app, monkeypatch):
    """JSON Schema accepts 261.0 as an integer; upstream must still see 261."""
    app = public_app
    from flask import request

    from mcp_sidecar.server import create_app
    from tests.test_mcp_sidecar_postgres import operator_server

    seen = []

    @app.before_request
    def record():
        seen.append(request.full_path.rstrip("?"))

    monkeypatch.setenv("OPENLEG_MCP_ENABLED", "true")
    with operator_server(app) as origin:
        monkeypatch.setenv("OPENLEG_OPERATOR_ORIGIN", origin)
        sidecar = create_app()

        async def check():
            async with (
                sidecar.router.lifespan_context(sidecar),
                session(sidecar) as sdk,
            ):
                as_float = await sdk.call_tool("get_municipalities_bfs", {"bfs": 261.0})
                as_int = await sdk.call_tool("get_municipalities_bfs", {"bfs": 261})
                assert not as_float.isError
                assert as_float.structuredContent == as_int.structuredContent
                await sdk.call_tool("get_tariffs", {"year": 2025.0})

        asyncio.run(check())

    assert seen[0] == "/api/v1/municipalities/261"
    assert seen[-1] == "/api/v1/tariffs?year=2025"


def peer_app(app, address):
    async def incoming(scope, receive, send):
        await app({**scope, "client": (address, 12345)}, receive, send)

    return incoming


def test_public_budget_is_per_peer_before_upstream(public_app, monkeypatch):
    from flask import request

    from mcp_sidecar.server import create_app
    from tests.test_mcp_sidecar_postgres import operator_server

    seen = []

    @public_app.before_request
    def capture():
        seen.append(request.path)

    monkeypatch.setenv("OPENLEG_MCP_ENABLED", "true")
    monkeypatch.setenv("OPENLEG_MCP_PUBLIC_CALLS", "1")
    with operator_server(public_app) as origin:
        monkeypatch.setenv("OPENLEG_OPERATOR_ORIGIN", origin)
        app = create_app()

        async def check():
            async with app.router.lifespan_context(app):
                async with session(peer_app(app, "192.0.2.1")) as sdk:
                    assert not (await sdk.call_tool("get_municipalities", {})).isError
                    limited = await sdk.call_tool("get_tariffs", {})
                    assert limited.isError
                    assert limited.content[0].text == "rate_limited"
                    assert limited.structuredContent["code"] == "rate_limited"
                    assert 1 <= limited.structuredContent["retry_after"] <= 60
                    from mcp.shared.exceptions import McpError

                    # The public budget does not apply to operator tools,
                    # which still need a bearer.
                    with pytest.raises(McpError) as operator:
                        await sdk.call_tool(
                            "list_billing_periods", {"community_id": "one"}
                        )
                    assert (
                        operator.value.error.data["code"] == "authentication_required"
                    )
                # A new stateless SDK session cannot reset the same peer's budget.
                async with session(peer_app(app, "192.0.2.1")) as sdk:
                    assert (await sdk.call_tool("get_municipalities", {})).isError
                async with session(peer_app(app, "192.0.2.2")) as sdk:
                    assert not (await sdk.call_tool("get_municipalities", {})).isError
            assert seen == ["/api/v1/municipalities"] * 2

        asyncio.run(check())


@pytest.mark.parametrize("trusted", ["", "192.0.2.99", "192.0.2.1"])
def test_only_named_proxy_can_supply_client_ip(public_app, monkeypatch, trusted):
    from mcp_sidecar.server import create_app
    from tests.test_mcp_sidecar_postgres import operator_server

    monkeypatch.setenv("OPENLEG_MCP_ENABLED", "true")
    monkeypatch.setenv("OPENLEG_MCP_PUBLIC_CALLS", "1")
    monkeypatch.setenv("OPENLEG_MCP_TRUSTED_PROXY", trusted)
    with operator_server(public_app) as origin:
        monkeypatch.setenv("OPENLEG_OPERATOR_ORIGIN", origin)
        app = create_app()

        async def check():
            async with app.router.lifespan_context(app):
                for index in (1, 2):
                    async with session(
                        peer_app(app, "192.0.2.1"),
                        {
                            "X-Real-IP": f"198.51.100.{index}",
                            "X-Forwarded-For": f"203.0.113.{index}",
                        },
                    ) as sdk:
                        result = await sdk.call_tool("get_municipalities", {})
                        assert result.isError == (index == 2 and trusted != "192.0.2.1")

        asyncio.run(check())


def test_expiry_capacity_and_operator_independence(public_app, monkeypatch):
    from flask import jsonify, request

    from mcp_sidecar import limits
    from mcp_sidecar.server import create_app
    from tests.test_mcp_sidecar_postgres import operator_server

    @public_app.get("/api/operator/v1/communities/one/billing/periods")
    def operator():
        assert request.headers["Authorization"] == "Bearer olk_test"
        return jsonify(items=[], next_cursor=None)

    # A local clock seam leaves the SDK's event loop clock alone.
    from types import SimpleNamespace

    clock = SimpleNamespace(monotonic=lambda: now[0])
    now = [100.0]
    monkeypatch.setattr(limits, "time", clock)
    monkeypatch.setenv("OPENLEG_MCP_ENABLED", "true")
    monkeypatch.setenv("OPENLEG_MCP_PUBLIC_CALLS", "1")
    monkeypatch.setenv("OPENLEG_MCP_PUBLIC_WINDOW_SECONDS", "10")
    monkeypatch.setenv("OPENLEG_MCP_PUBLIC_MAX_CLIENTS", "2")
    with operator_server(public_app) as origin:
        monkeypatch.setenv("OPENLEG_OPERATOR_ORIGIN", origin)
        app = create_app()

        async def call(peer, tool="get_municipalities", args=None):
            async with session(
                peer_app(app, peer), {"Authorization": "Bearer olk_test"}
            ) as sdk:
                return await sdk.call_tool(tool, args or {})

        async def check():
            async with app.router.lifespan_context(app):
                assert not (await call("192.0.2.1")).isError
                now[0] = 105.0
                assert not (await call("192.0.2.2")).isError
                for index in range(3, 15):
                    result = await call(f"192.0.2.{index}")
                    assert result.structuredContent == {
                        "code": "rate_limited",
                        "field": None,
                        "retryable": True,
                        "retry_after": 5,
                    }
                assert (await call("192.0.2.1")).isError
                for _ in range(3):
                    result = await call(
                        "192.0.2.1", "list_billing_periods", {"community_id": "one"}
                    )
                    assert not result.isError
                    assert result.structuredContent == {
                        "items": [],
                        "next_cursor": None,
                    }
                now[0] = 109.2
                assert (await call("192.0.2.1")).structuredContent["retry_after"] == 1
                now[0] = 110.0
                assert not (await call("192.0.2.3")).isError
                # The second client's unexpired budget survives eviction.
                assert (await call("192.0.2.2")).structuredContent["retry_after"] == 5
                now[0] = 120.0
                assert not (await call("192.0.2.1")).isError

        asyncio.run(check())


@pytest.mark.parametrize(
    "header",
    [
        [("X-Real-IP", "bad")],
        [("X-Real-IP", "198.51.100.1, 198.51.100.2")],
        [("X-Real-IP", "198.51.100.1"), ("X-Real-IP", "198.51.100.2")],
        [("X-Forwarded-For", "198.51.100.1")],
    ],
)
def test_invalid_proxy_headers_keep_peer_budget(public_app, monkeypatch, header):
    from mcp_sidecar.server import create_app
    from tests.test_mcp_sidecar_postgres import operator_server

    monkeypatch.setenv("OPENLEG_MCP_ENABLED", "true")
    monkeypatch.setenv("OPENLEG_MCP_PUBLIC_CALLS", "1")
    monkeypatch.setenv("OPENLEG_MCP_TRUSTED_PROXY", "192.0.2.1")
    with operator_server(public_app) as origin:
        monkeypatch.setenv("OPENLEG_OPERATOR_ORIGIN", origin)
        app = create_app()

        async def check():
            async with app.router.lifespan_context(app):
                async with session(peer_app(app, "192.0.2.1")) as sdk:
                    assert not (await sdk.call_tool("get_municipalities", {})).isError
                async with session(peer_app(app, "192.0.2.1"), header) as sdk:
                    assert (await sdk.call_tool("get_municipalities", {})).isError

        asyncio.run(check())


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("OPENLEG_MCP_PUBLIC_CALLS", "0"),
        ("OPENLEG_MCP_PUBLIC_WINDOW_SECONDS", "-1"),
        ("OPENLEG_MCP_PUBLIC_MAX_CLIENTS", "0"),
        ("OPENLEG_MCP_PUBLIC_CALLS", "oops"),
        ("OPENLEG_MCP_TRUSTED_PROXY", "*"),
    ],
)
def test_invalid_limit_settings_fail_startup(monkeypatch, setting, value):
    from mcp_sidecar.server import create_app

    monkeypatch.setenv("OPENLEG_MCP_ENABLED", "true")
    monkeypatch.setenv(setting, value)
    with pytest.raises(ValueError):
        create_app()


def test_entry_point_preserves_connecting_peer(monkeypatch):
    import runpy
    from unittest.mock import Mock

    import uvicorn

    monkeypatch.setenv("OPENLEG_MCP_ENABLED", "true")
    run = Mock()
    monkeypatch.setattr(uvicorn, "run", run)
    runpy.run_module("mcp_sidecar", run_name="__main__")
    assert run.call_args.kwargs["proxy_headers"] is False
