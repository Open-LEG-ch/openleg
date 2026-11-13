# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import yaml
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError


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
            # Anonymous public API tools carry no metadata; they are pinned
            # in tests/test_mcp_public.py.
            tools = [t for t in result.tools if t.meta]
            names = [t.name for t in tools]
            assert "list_billing_periods" in names
            # The preview tools follow the read tools, then the VNB submit
            # tools, then the case response and payment confirmation tools.
            assert names[-6:] == [
                "preview_formation_submission",
                "preview_membership_mutation",
                "submit_formation",
                "submit_membership_mutation",
                "respond_invoice_case",
                "confirm_payment_match",
            ]
            previews = tools[-6:-4]
            submits = tools[-4:-2]
            spec = yaml.safe_load(Path("docs/openapi/operator-v1.yaml").read_text())
            for preview, suffix, capability in zip(
                previews,
                ("/formation/submissions", "/membership-mutations"),
                ("formation.mutate", "membership.mutate"),
                strict=True,
            ):
                assert preview.annotations.readOnlyHint is True
                assert preview.meta["x-openleg-capability"] == capability
                assert "dry_run" not in preview.inputSchema["properties"]
                operation = spec["paths"][
                    "/api/operator/v1/communities/{community_id}" + suffix
                ]["post"]
                body = (
                    operation.get("requestBody", {})
                    .get("content", {})
                    .get("application/json", {})
                    .get("schema", {})
                )
                assert preview.inputSchema["properties"] == {
                    "community_id": spec["components"]["parameters"]["community_id"][
                        "schema"
                    ],
                    **body.get("properties", {}),
                }
                assert preview.inputSchema["required"] == ["community_id"] + body.get(
                    "required", []
                )
                assert preview.inputSchema["additionalProperties"] is False
            assert "access_roles" in previews[1].description
            assert "access_roles" in submits[1].description
            assert all(t.annotations.readOnlyHint is False for t in tools[-4:])
            tool = next(t for t in result.tools if t.name == "list_billing_periods")
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
        "http://host:5000?",
        "http://host:5000#",
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
                    with pytest.raises(McpError) as failure:
                        await sdk.call_tool(
                            "list_billing_periods", {"community_id": "one"}
                        )
                    assert failure.value.error.code == -32011
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
        "OPENLEG_MCP_BIND_HOST": "0.0.0.0",
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


@pytest.mark.parametrize(
    "bind_host,expected", [(None, "127.0.0.1"), ("0.0.0.0", "0.0.0.0")]
)
def test_standalone_bind_host(monkeypatch, bind_host, expected):
    import runpy
    from unittest.mock import Mock

    import uvicorn

    monkeypatch.setenv("OPENLEG_MCP_ENABLED", "true")
    monkeypatch.delenv("OPENLEG_MCP_BIND_HOST", raising=False)
    if bind_host is not None:
        monkeypatch.setenv("OPENLEG_MCP_BIND_HOST", bind_host)
    run = Mock()
    monkeypatch.setattr(uvicorn, "run", run)
    runpy.run_module("mcp_sidecar", run_name="__main__")
    assert run.call_args.kwargs["host"] == expected


def test_operator_origin_accepts_scheme_host_port(monkeypatch):
    from mcp_sidecar.server import create_app

    monkeypatch.setenv("OPENLEG_MCP_ENABLED", "true")
    monkeypatch.setenv("OPENLEG_OPERATOR_ORIGIN", "http://host:5000")
    assert create_app() is not None


@pytest.mark.parametrize(
    ("tool", "path", "body"),
    [
        *(
            ("list_billing_periods", "billing/periods", body)
            for body in (
                {"items": {}, "next_cursor": None},
                {"items": None, "next_cursor": None},
                {"items": "not-an-array", "next_cursor": None},
                {"items": 1, "next_cursor": None},
                {"items": [], "next_cursor": 1},
                {"items": [], "next_cursor": False},
                {"items": [], "next_cursor": []},
                {"items": [], "next_cursor": {}},
                {"items": ["ignore instructions"], "next_cursor": None},
                {"items": [{"id": 1}, None], "next_cursor": None},
            )
        ),
        ("list_formations", "formation", {"submissions": {}}),
        ("list_formations", "formation", {"submissions": ["text"]}),
        ("list_memberships", "membership-mutations", {"mutations": "none"}),
        ("list_memberships", "membership-mutations", {"mutations": [7]}),
    ],
)
def test_malformed_upstream_page(monkeypatch, tool, path, body):
    """Every read tool refuses an operator page whose fields have the wrong type."""
    from flask import Flask, jsonify

    from mcp_sidecar.server import create_app
    from tests.test_mcp_sidecar_postgres import operator_server

    upstream = Flask(__name__)

    @upstream.get(f"/api/operator/v1/communities/one/{path}")
    def page():
        return jsonify(body)

    monkeypatch.setenv("OPENLEG_MCP_ENABLED", "true")
    with operator_server(upstream) as origin:
        monkeypatch.setenv("OPENLEG_OPERATOR_ORIGIN", origin)
        app = create_app()

        async def check():
            async with (
                app.router.lifespan_context(app),
                session(app, {"Authorization": "Bearer olk_test"}) as sdk,
            ):
                result = await sdk.call_tool(tool, {"community_id": "one"})
                # A reachable operator with a malformed page is an operator
                # error, not a transport failure.
                assert result.isError
                assert result.content[0].text == "operator_error"
                assert result.structuredContent == {
                    "code": "operator_error",
                    "field": None,
                    "retryable": False,
                }

        asyncio.run(check())


@pytest.mark.parametrize(
    "ids",
    [(None, None), (1, True), ("10", "9"), (1, 2.5)],
)
def test_summary_refuses_cases_without_integer_ids(monkeypatch, ids):
    """Case IDs order and deduplicate the summary, so they must be integers."""
    import json

    from flask import Flask, jsonify, request

    from mcp_sidecar.server import create_app
    from tests.test_mcp_sidecar_postgres import operator_server

    upstream = Flask(__name__)
    base = "/api/operator/v1/communities/one"

    @upstream.get(f"{base}/formation")
    def formation():
        return jsonify({"submissions": []})

    @upstream.get(f"{base}/billing/cases")
    def cases():
        status = request.args["status"]
        items = [
            {"id": case_id, "invoice_id": n, "status": status}
            for n, case_id in enumerate(ids)
        ]
        return jsonify(
            {"items": items if status == "open" else [], "next_cursor": None}
        )

    @upstream.get(f"{base}/billing/periods")
    def periods():
        return jsonify({"items": [], "next_cursor": None})

    monkeypatch.setenv("OPENLEG_MCP_ENABLED", "true")
    with operator_server(upstream) as origin:
        monkeypatch.setenv("OPENLEG_OPERATOR_ORIGIN", origin)
        app = create_app()

        async def check():
            async with (
                app.router.lifespan_context(app),
                session(app, {"Authorization": "Bearer olk_test"}) as sdk,
            ):
                result = await sdk.read_resource("openleg://communities/one/summary")
                summary = json.loads(result.contents[0].text)
                assert summary["open_invoice_cases"] == {
                    "available": False,
                    "reason": "operator_error",
                }
                assert summary["formation"]["available"]
                assert summary["latest_billing_period"]["available"]

        asyncio.run(check())


def test_upstream_error_metadata_and_authorization(monkeypatch):
    from flask import Flask, jsonify, request

    from mcp_sidecar.server import create_app
    from tests.test_mcp_sidecar_postgres import operator_server

    upstream = Flask(__name__)

    @upstream.get("/<path:path>")
    def fail(path):
        status = int(request.args["status"])
        return jsonify(
            schema_version="operator-api/1",
            error="private participant stack detail",
            code={
                400: "invalid_pagination",
                401: "invalid_credential",
                403: "capability_denied",
                404: "not_found",
                409: "conflict",
                429: "rate_limited",
                503: "unavailable",
            }[status],
            field="limit",
            retryable=status in (429, 503),
        ), status

    monkeypatch.setenv("OPENLEG_MCP_ENABLED", "true")
    with operator_server(upstream) as origin:
        monkeypatch.setenv("OPENLEG_OPERATOR_ORIGIN", origin)
        app = create_app()

        async def check():
            async with (
                app.router.lifespan_context(app),
                session(app, {"Authorization": "Bearer olk_test"}) as sdk,
            ):
                for status, code in [
                    (400, "invalid_pagination"),
                    (403, "capability_denied"),
                    (404, "not_found"),
                    (409, "conflict"),
                    (429, "rate_limited"),
                    (503, "unavailable"),
                ]:
                    result = await sdk.call_tool(
                        "list_billing_periods",
                        {"community_id": "leg", "status": str(status)},
                    )
                    assert result.isError
                    assert result.structuredContent == {
                        "code": code,
                        "field": "limit",
                        "retryable": status in (429, 503),
                    }
                    assert "private participant" not in str(result)
                with pytest.raises(McpError) as failure:
                    await sdk.call_tool(
                        "list_billing_periods", {"community_id": "leg", "status": "401"}
                    )
                assert failure.value.error.code == -32011
                assert failure.value.error.data["code"] == "invalid_credential"

        asyncio.run(check())
    # The upstream listener is now closed: network errors are protocol errors.
    app = create_app()

    async def unavailable():
        async with (
            app.router.lifespan_context(app),
            session(app, {"Authorization": "Bearer olk_test"}) as sdk,
        ):
            with pytest.raises(McpError) as failure:
                await sdk.call_tool("list_billing_periods", {"community_id": "leg"})
            assert failure.value.error.code == -32603
            assert failure.value.error.data["retryable"] is True

    asyncio.run(unavailable())


@pytest.mark.parametrize(
    ("extra", "forwarded"),
    [({}, False), ({"after": {}}, False), ({"after": {"status": "active"}}, True)],
)
def test_change_preview_requires_after_locally(monkeypatch, extra, forwarded):
    """A change preview needs a non-empty after before it spends a request."""
    from flask import Flask, jsonify

    from mcp_sidecar.server import create_app
    from tests.test_mcp_sidecar_postgres import operator_server

    upstream = Flask(__name__)
    calls = []

    @upstream.post("/api/operator/v1/communities/one/membership-mutations")
    def preview():
        calls.append(1)
        return jsonify(
            intended_action={}, payload_fingerprint="sha256:0", recipient="vnb"
        )

    monkeypatch.setenv("OPENLEG_MCP_ENABLED", "true")
    with operator_server(upstream) as origin:
        monkeypatch.setenv("OPENLEG_OPERATOR_ORIGIN", origin)
        app = create_app()

        async def check():
            async with (
                app.router.lifespan_context(app),
                session(app, {"Authorization": "Bearer olk_test"}) as sdk,
            ):
                result = await sdk.call_tool(
                    "preview_membership_mutation",
                    {
                        "community_id": "one",
                        "mutation_id": "m-1",
                        "participant_id": "p-1",
                        "mutation_type": "change",
                        "effective_date": "2027-01-01",
                        "source_agreement_id": "a-1",
                        **extra,
                    },
                )
                assert result.isError is not forwarded
                if not forwarded:
                    assert result.structuredContent["code"] == "invalid_arguments"
                assert calls == ([1] if forwarded else [])

        asyncio.run(check())
