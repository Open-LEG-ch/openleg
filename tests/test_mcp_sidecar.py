# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import yaml
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def test_disabled_by_default(monkeypatch):
    from mcp_sidecar.server import create_app

    monkeypatch.delenv("OPENLEG_MCP_ENABLED", raising=False)
    with pytest.raises(RuntimeError, match="disabled"):
        create_app()


@asynccontextmanager
async def session(app, headers=None, *, expose_http=False):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), headers=headers
    ) as client:
        async with streamable_http_client(
            "http://localhost/mcp", http_client=client
        ) as (read, write, _):
            async with ClientSession(read, write) as sdk:
                await sdk.initialize()
                yield (sdk, client) if expose_http else sdk


def test_contract_tool(monkeypatch):
    from mcp_sidecar.server import create_app

    monkeypatch.setenv("OPENLEG_MCP_ENABLED", "true")
    monkeypatch.setenv("OPENLEG_OPERATOR_ORIGIN", "http://localhost:5000")
    app = create_app()

    async def check():
        async with app.router.lifespan_context(app), session(app) as sdk:
            result = await sdk.list_tools()
            assert [t.name for t in result.tools] == ["list_billing_periods"]
            tool = result.tools[0]
            spec = yaml.safe_load(Path("docs/openapi/operator-v1.yaml").read_text())
            op = spec["paths"][
                "/api/operator/v1/communities/{community_id}/billing/periods"
            ]["get"]
            params = [
                spec["components"]["parameters"][p["$ref"].split("/")[-1]]
                for p in op["parameters"]
            ]
            assert tool.inputSchema == {
                "type": "object",
                "properties": {p["name"]: p["schema"] for p in params},
                "required": ["community_id"],
                "additionalProperties": False,
            }
            assert tool.annotations.readOnlyHint is True
            assert (
                tool.meta["x-openleg-capability"]
                == op["x-openleg-capability"]
                == "billing.read"
            )

    asyncio.run(check())


@pytest.mark.parametrize(
    "origin",
    [
        "https://user:secret@example.org",
        "https://example.org/api",
        "https://example.org?url=evil",
        "file:///tmp/socket",
        "https://example.org/#fragment",
    ],
)
def test_origin_rejects_non_origins(monkeypatch, origin):
    from mcp_sidecar.server import create_app

    monkeypatch.setenv("OPENLEG_MCP_ENABLED", "true")
    monkeypatch.setenv("OPENLEG_OPERATOR_ORIGIN", origin)
    with pytest.raises(ValueError):
        create_app()


def test_http_trust_boundary(monkeypatch, caplog):
    import logging

    from flask import Flask, jsonify, redirect, request

    from mcp_sidecar.server import create_app
    from tests.test_mcp_sidecar_postgres import operator_server

    caplog.set_level(logging.DEBUG)
    upstream = Flask(__name__)
    seen = []
    token = "olk_sensitive_request_credential"

    @upstream.route("/<path:path>")
    def handle(path):
        seen.append(
            (path, request.headers.get("Authorization"), request.headers.get("Cookie"))
        )
        if request.args.get("status") == "redirect":
            return redirect("/outside")
        if request.args.get("status") == "echo":
            return jsonify(items=[token], next_cursor=None)
        if request.args.get("status") == "other_token":
            return jsonify(items=["olk_other_sensitive_credential"], next_cursor=None)
        if request.args.get("status") == "failure":
            return jsonify(error=token), 500
        result = jsonify(items=[{"community_id": path.split("/")[4]}], next_cursor=None)
        result.set_cookie("secret", token)
        return result

    monkeypatch.setenv("OPENLEG_MCP_ENABLED", "true")
    with operator_server(upstream) as origin:
        monkeypatch.setenv("OPENLEG_OPERATOR_ORIGIN", origin)
        app = create_app()

        async def check():
            async with app.router.lifespan_context(app):

                async def caller(credential, community):
                    async with session(
                        app, {"Authorization": "Bearer " + credential}
                    ) as sdk:
                        for _ in range(3):
                            result = await sdk.call_tool(
                                "list_billing_periods", {"community_id": community}
                            )
                            assert not result.isError
                            assert result.structuredContent["items"] == [
                                {"community_id": community}
                            ]

                await asyncio.gather(caller(token, "one"), caller("olk_second", "two"))
                async with session(app, {"Authorization": "Bearer " + token}) as sdk:
                    for status in ("redirect", "echo", "other_token", "failure"):
                        result = await sdk.call_tool(
                            "list_billing_periods",
                            {"community_id": "one", "status": status},
                        )
                        assert result.isError
                        assert token not in str(result)
                    count = len(seen)
                    for community in (
                        "../admin",
                        "//evil.example",
                        "%2e%2e",
                        "one/../../admin",
                        "one\\admin",
                    ):
                        result = await sdk.call_tool(
                            "list_billing_periods", {"community_id": community}
                        )
                        assert result.isError
                    result = await sdk.call_tool(
                        "list_billing_periods",
                        {"community_id": "one", "url": "https://evil.example"},
                    )
                    assert result.isError
                    assert len(seen) == count
                async with session(app) as sdk:
                    result = await sdk.call_tool(
                        "list_billing_periods", {"community_id": "one"}
                    )
                    assert result.isError
                    assert len(seen) == count

        asyncio.run(check())
    assert all(
        path.startswith("api/operator/v1/communities/")
        and path.endswith("/billing/periods")
        for path, _, _ in seen
    )
    assert all(
        auth == ("Bearer olk_second" if "/two/" in path else "Bearer " + token)
        for path, auth, _ in seen
    )
    assert all(cookie is None for _, _, cookie in seen)
    assert token not in caplog.text


def test_package_has_no_database_access():
    import ast

    for path in Path("mcp_sidecar").glob("*.py"):
        source = path.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(
                    n.name.split(".")[0]
                    not in {"database", "store", "app_config", "app", "psycopg2"}
                    for n in node.names
                )
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in {
                    "database",
                    "store",
                    "app_config",
                    "app",
                    "psycopg2",
                }
        assert "DATABASE_URL" not in source
        assert "POSTGRES" not in source


def test_local_packaging_is_opt_in():
    config = yaml.safe_load(Path("compose.mcp.yml").read_text())
    for service in config["services"].values():
        assert service["profiles"] == ["mcp"]
        assert "env_file" not in service
    env = config["services"]["mcp"]["environment"]
    assert env == {
        "OPENLEG_MCP_ENABLED": "${OPENLEG_MCP_ENABLED:-false}",
        "OPENLEG_OPERATOR_ORIGIN": "http://flask:5000",
        "OPENLEG_MCP_ALLOWED_HOSTS": "localhost:8081,127.0.0.1:8081",
        "OPENLEG_MCP_ALLOWED_ORIGINS": (
            "http://localhost:8081,http://127.0.0.1:8081,"
            "http://localhost:6274,http://127.0.0.1:6274"
        ),
    }
    assert (
        "mcp" not in yaml.safe_load(Path("docker-compose.yml").read_text())["services"]
    )
    assert "mcp" not in Path("Caddyfile").read_text()
    assert "handle /mcp" in Path("Caddyfile.mcp").read_text()
    assert "-r requirements-mcp.txt" in Path("requirements-dev.txt").read_text()
    assert "mcp" not in Path("requirements.txt").read_text()


@pytest.mark.parametrize(
    "host,origin,status",
    [
        ("attacker.example", "http://localhost:6274", 421),
        ("localhost:8081", "http://attacker.example", 403),
        ("localhost:8081", "null", 403),
        ("localhost:8081", "http://localhost:8081", 200),
        ("localhost:8081", "http://localhost:6274", 200),
        ("localhost:8081", None, 200),
    ],
)
@pytest.mark.parametrize("custom_proxy", [False, True])
def test_transport_header_validation(monkeypatch, host, origin, status, custom_proxy):
    from mcp_sidecar.server import create_app

    monkeypatch.setenv("OPENLEG_MCP_ENABLED", "true")
    # Use the actual local proxy configuration, including Inspector's origin.
    config = yaml.safe_load(Path("compose.mcp.yml").read_text())
    for name, value in config["services"]["mcp"]["environment"].items():
        if name.startswith("OPENLEG_MCP_ALLOWED_"):
            if custom_proxy:
                value = value.replace("localhost", "proxy.example")
            monkeypatch.setenv(name, value)
    if custom_proxy:
        host = host.replace("localhost", "proxy.example")
        if origin is not None:
            origin = origin.replace("localhost", "proxy.example")
    app = create_app()

    async def check():
        headers = {"Host": host, "Accept": "application/json, text/event-stream"}
        if origin is not None:
            headers["Origin"] = origin
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client,
        ):
            response = await client.post(
                "http://localhost:8081/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"},
                    },
                },
            )
            assert response.status_code == status
            if status == 200:
                assert response.json()["result"]["serverInfo"]["name"] == "openleg-mcp"

    asyncio.run(check())


@pytest.mark.parametrize(
    "name", ["OPENLEG_MCP_ALLOWED_HOSTS", "OPENLEG_MCP_ALLOWED_ORIGINS"]
)
@pytest.mark.parametrize("value", ["", " , , "])
def test_empty_transport_allowlist_refuses_start(monkeypatch, name, value):
    from mcp_sidecar.server import create_app

    monkeypatch.setenv("OPENLEG_MCP_ENABLED", "true")
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        create_app()


def test_exception_and_stack_logs_redact_tokens(monkeypatch):
    import io
    import logging

    from mcp_sidecar.server import redact_transport_logs

    # Restore the factory after this test, including on the pre-fix failure.
    monkeypatch.setattr(logging, "_logRecordFactory", logging.getLogRecordFactory())
    redact_transport_logs()
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logger = logging.getLogger("sidecar-diagnostic")
    monkeypatch.setattr(logger, "handlers", [handler])
    monkeypatch.setattr(logger, "propagate", False)
    monkeypatch.setattr(logger, "disabled", False)
    token = "olk_synthetic_exception_secret"
    try:
        raise ValueError("upstream failed with " + token)
    except ValueError:
        logger.exception("request failed")
    # Exercise stack_info independently of the exception path.
    record = logger.makeRecord(
        logger.name,
        logging.ERROR,
        __file__,
        1,
        "stack diagnostic",
        (),
        None,
        sinfo="Stack (most recent call last):\n  " + token,
    )
    logger.handle(record)
    rendered = output.getvalue()
    assert token not in rendered
    assert "ValueError: upstream failed with [redacted]" in rendered
    assert "Traceback (most recent call last):" in rendered
    assert "ERROR sidecar-diagnostic: request failed" in rendered
    assert "Stack (most recent call last):\n  [redacted]" in rendered


def test_redaction_preserves_unrelated_record_semantics(monkeypatch):
    import logging
    import sys

    from mcp_sidecar.server import redact_transport_logs

    monkeypatch.setattr(logging, "_logRecordFactory", logging.getLogRecordFactory())
    redact_transport_logs()
    logger = logging.getLogger("unrelated")
    try:
        raise ValueError("ordinary diagnostic")
    except ValueError:
        exception = sys.exc_info()
        record = logger.makeRecord(
            logger.name,
            logging.ERROR,
            __file__,
            1,
            "failed %s",
            ("request",),
            exception,
            sinfo="ordinary stack",
        )
    assert record.msg == "failed %s"
    assert record.args == ("request",)
    assert record.exc_info is exception
    assert record.exc_text is None
    assert record.stack_info == "ordinary stack"
