# SPDX-License-Identifier: AGPL-3.0-or-later
"""Explicit anonymous read surface from the committed public contract."""

import json
from pathlib import Path

from mcp import types

PUBLIC_OPERATION_IDS = (
    "get_municipalities",
    "get_municipalities_bfs",
    "get_municipalities_bfs_tariffs",
    "get_municipalities_bfs_solar",
    "get_tariffs",
    "get_search",
    "get_rankings",
    "get_site_rankings",
    "get_site_rankings_movers",
)


def public_operations():
    spec = json.loads(
        (Path(__file__).parents[1] / "docs/openapi/public-v1.json").read_text()
    )
    operations = {}
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            name = op.get("operationId")
            if name not in PUBLIC_OPERATION_IDS:
                continue
            if method != "get":
                raise RuntimeError("Public MCP operations must be GET")
            parameters = op.get("parameters", [])
            schema = {
                "type": "object",
                "properties": {p["name"]: p["schema"] for p in parameters},
                "required": [p["name"] for p in parameters if p.get("required")],
                "additionalProperties": False,
            }
            operations[name] = (
                "/api/v1" + path,
                {p["name"] for p in parameters if p["in"] == "path"},
                types.Tool(
                    name=name,
                    description=op["summary"],
                    inputSchema=schema,
                    annotations=types.ToolAnnotations(
                        readOnlyHint=True, destructiveHint=False, idempotentHint=True
                    ),
                ),
            )
    if set(operations) != set(PUBLIC_OPERATION_IDS):
        raise RuntimeError("Missing public MCP contract operation")
    return operations
