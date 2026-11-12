# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stateless MCP transport; credentials belong to individual HTTP requests."""

import copy
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import yaml
from jsonschema import Draft202012Validator
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.routing import Route


def redact_transport_logs():
    # Process-wide because HTTP transport loggers are created lazily. No token
    # registry: redact the credential format without storing any credentials.
    previous = logging.getLogRecordFactory()
    if getattr(previous, "openleg_redaction", False):
        return

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        # Render a copy so exception caching and message interpolation leave
        # unrelated records untouched. Flatten only credential-bearing records,
        # including exception chains and stack info, before handlers format them.
        rendered = logging.Formatter().format(copy.copy(record))
        redacted = re.sub(r"olk_[A-Za-z0-9_-]+", "[redacted]", rendered)
        if redacted != rendered:
            record.msg = redacted
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return record

    factory.openleg_redaction = True
    logging.setLogRecordFactory(factory)


def allowlist(name, default):
    values = [value.strip() for value in os.environ.get(name, default).split(",")]
    values = [value for value in values if value]
    if not values:
        raise ValueError(f"{name} must not be empty")
    return values


def operation():
    spec = yaml.safe_load(
        (Path(__file__).parents[1] / "docs/openapi/operator-v1.yaml").read_text()
    )
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            if op.get("operationId") == "listPeriods":
                parameters = [
                    spec["components"]["parameters"][p["$ref"].split("/")[-1]]
                    for p in op["parameters"]
                ]
                schema = {
                    "type": "object",
                    "properties": {p["name"]: p["schema"] for p in parameters},
                    "required": [p["name"] for p in parameters if p.get("required")],
                    "additionalProperties": False,
                }
                return path, method, op["x-openleg-capability"], schema
    raise RuntimeError("Missing listPeriods contract")


def create_app():
    if os.environ.get("OPENLEG_MCP_ENABLED", "false") != "true":
        raise RuntimeError("MCP sidecar is disabled")
    redact_transport_logs()
    path, method, capability, schema = operation()
    origin = os.environ.get("OPENLEG_OPERATOR_ORIGIN", "http://localhost:5000")
    parsed = urlsplit(origin)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
        or "?" in origin
        or "#" in origin
    ):
        raise ValueError("Operator origin must contain only scheme, host and port")
    if (
        method != "get"
        or path != "/api/operator/v1/communities/{community_id}/billing/periods"
    ):
        raise RuntimeError("Unexpected listPeriods operation")
    server = Server("openleg-mcp")

    @server.list_tools()
    async def list_tools():
        return [
            types.Tool(
                name="list_billing_periods",
                inputSchema=schema,
                annotations=types.ToolAnnotations(
                    readOnlyHint=True, destructiveHint=False, idempotentHint=True
                ),
                _meta={"x-openleg-capability": capability},
            )
        ]

    def error(code):
        return types.CallToolResult(
            isError=True, content=[types.TextContent(type="text", text=code)]
        )

    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        # Do not echo SDK validation exceptions: they contain caller input.
        if name != "list_billing_periods" or not Draft202012Validator(schema).is_valid(
            arguments
        ):
            return error("invalid_arguments")
        arguments = {
            key: int(value)
            if schema["properties"][key].get("type") == "integer"
            and isinstance(value, float)
            else value
            for key, value in arguments.items()
        }
        community = arguments["community_id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", community):
            return error("invalid_arguments")
        authorization = server.request_context.request.headers.get("authorization", "")
        if not re.fullmatch(r"Bearer olk_[A-Za-z0-9_-]+", authorization):
            return error("unauthorized")
        # A fresh client also prevents upstream cookies from crossing callers.
        try:
            async with httpx.AsyncClient(
                follow_redirects=False, trust_env=False, timeout=10
            ) as client:
                response = await client.get(
                    origin + path.format(community_id=community),
                    params={k: v for k, v in arguments.items() if k != "community_id"},
                    headers={"Authorization": authorization},
                )
            if response.status_code != 200:
                return error(
                    {
                        401: "unauthorized",
                        403: "forbidden",
                        404: "not_found",
                        429: "rate_limited",
                    }.get(response.status_code, "operator_error")
                )
            body = response.json()
            result = {key: body[key] for key in ("items", "next_cursor")}
            if not isinstance(result["items"], list) or (
                result["next_cursor"] is not None
                and not isinstance(result["next_cursor"], str)
            ):
                raise ValueError("Malformed operator page")
            if re.search(r"olk_[A-Za-z0-9_-]+", json.dumps(result)):
                return error("operator_error")
            return result
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return error("operator_unavailable")

    manager = StreamableHTTPSessionManager(
        server,
        stateless=True,
        json_response=True,
        security_settings=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowlist(
                "OPENLEG_MCP_ALLOWED_HOSTS",
                "localhost,127.0.0.1,localhost:8000,127.0.0.1:8000,"
                "localhost:8081,127.0.0.1:8081",
            ),
            allowed_origins=allowlist(
                "OPENLEG_MCP_ALLOWED_ORIGINS",
                "http://localhost:8000,http://127.0.0.1:8000,"
                "http://localhost:8081,http://127.0.0.1:8081,"
                "http://localhost:6274,http://127.0.0.1:6274",
            ),
        ),
    )

    @asynccontextmanager
    async def lifespan(app):
        async with manager.run():
            yield

    class Endpoint:
        async def __call__(self, scope, receive, send):
            await manager.handle_request(scope, receive, send)

    return Starlette(
        routes=[Route("/mcp", endpoint=Endpoint(), methods=["GET", "POST", "DELETE"])],
        lifespan=lifespan,
    )
