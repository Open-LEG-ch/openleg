# SPDX-License-Identifier: AGPL-3.0-or-later
"""Explicit payment write tool; the operator contract owns its input types."""

from mcp import types


def payment_tool(spec):
    path = "/api/operator/v1/communities/{community_id}/payments/matches/{entry_id}/confirm"
    operation = spec["paths"][path]["post"]
    properties = {}
    required = []
    for parameter in operation["parameters"]:
        if "$ref" in parameter:
            parameter = spec["components"]["parameters"][
                parameter["$ref"].split("/")[-1]
            ]
        name = parameter["name"]
        name = "idempotency_key" if name == "Idempotency-Key" else name
        properties[name] = parameter["schema"]
        if parameter.get("required"):
            required.append(name)
    body = operation["requestBody"]["content"]["application/json"]["schema"]
    properties.update(body["properties"])
    required.extend(body["required"])
    return path, types.Tool(
        name="confirm_payment_match",
        inputSchema={
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        annotations=types.ToolAnnotations(
            readOnlyHint=False, destructiveHint=True, idempotentHint=True
        ),
        _meta={
            "x-openleg-capability": operation["x-openleg-capability"],
            "operationId": operation["operationId"],
        },
    )
