# SPDX-License-Identifier: AGPL-3.0-or-later
"""Agent case responses through HTTP with real persistence."""

import pytest

from tests import operator_contract_helpers as helpers

interest_database = helpers.interest_database
operator_http = helpers.operator_http
pytestmark = pytest.mark.integration


def snapshot(http):
    tables = (
        "invoices",
        "invoice_lifecycle_events",
        "invoice_queries",
        "invoice_query_messages",
        "invoice_query_events",
        "operator_events",
        "operator_webhook_deliveries",
        "operator_action_idempotency",
    )
    with http.db.get_connection() as conn, conn.cursor() as cur:
        result = {}
        for table in tables:
            cur.execute(f"SELECT * FROM {table}")
            result[table] = cur.fetchall()
        return result


def test_preview_leaves_full_rows_unchanged(operator_http):
    http = operator_http
    ids = helpers.seed_operator_resources(http)
    before = snapshot(http)
    response = http.request(
        "POST",
        f"/billing/cases/{ids['case_id']}/responses?dry_run=true",
        json={"message": "Checked.", "status": "acknowledged"},
    )
    assert response.status_code == 200, response.json
    assert response.json == {
        "schema_version": "operator-api/1",
        "id": ids["case_id"],
        "previous_status": "open",
        "status": "acknowledged",
        "dry_run": True,
    }
    from tests.test_operator_contract import load_contract

    helpers.validate_response(
        load_contract(),
        "/api/operator/v1/communities/{community_id}/billing/cases/{case_id}/responses",
        "post",
        response,
    )
    assert snapshot(http) == before


@pytest.mark.parametrize(
    "query,extra",
    [
        ("?dry_run=TRUE", {}),
        ("?dry_run=1", {}),
        ("?dry_run=", {}),
        ("?dry_run=true&dry_run=true", {}),
        ("?dry_run=false&dry_run=true", {}),
        ("", {"dry_run": True}),
        ("?dry_run=true", {"dry_run": False}),
    ],
)
def test_invalid_preview_option_fails_closed(operator_http, query, extra):
    http = operator_http
    ids = helpers.seed_operator_resources(http)
    before = snapshot(http)
    response = http.request(
        "POST",
        f"/billing/cases/{ids['case_id']}/responses{query}",
        json={"message": "Checked.", "status": "resolved", **extra},
        headers={"Idempotency-Key": "case-key"},
    )
    assert response.status_code == 400
    assert response.json["code"] == "invalid_request"
    assert snapshot(http) == before


@pytest.mark.parametrize(
    "message,status",
    [
        ("", "resolved"),
        (123, "resolved"),
        ("ok", []),
        ("  ", "resolved"),
        ("x" * 4001, "resolved"),
        ("ok", "open"),
        ("ok", "cancelled"),
    ],
)
def test_preview_validates_message_and_transition(operator_http, message, status):
    http = operator_http
    ids = helpers.seed_operator_resources(http)
    before = snapshot(http)
    response = http.request(
        "POST",
        f"/billing/cases/{ids['case_id']}/responses?dry_run=true",
        json={"message": message, "status": status},
    )
    assert response.status_code == 409
    assert response.json["code"] == "invalid_case"
    assert snapshot(http) == before


def test_real_response_replay_conflict_and_agent(operator_http):
    http = operator_http
    ids = helpers.seed_operator_resources(http)
    with http.db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE operator_api_clients SET actor_kind='agent',agent_label='Case agent' WHERE id=%s",
            (http.credential["id"],),
        )
    suffix = f"/billing/cases/{ids['case_id']}/responses?dry_run=false"
    payload = {"message": "Checked.", "status": "resolved"}
    assert (
        http.request("POST", suffix, json=payload).json["code"]
        == "invalid_idempotency_key"
    )
    headers = {"Idempotency-Key": "case-key"}
    first = http.request("POST", suffix, json=payload, headers=headers)
    assert first.status_code == 200
    assert first.json["replayed"] is False
    before = snapshot(http)
    replay = http.request("POST", suffix, json=payload, headers=headers)
    assert replay.json == {**first.json, "replayed": True}
    assert snapshot(http) == before
    conflict = http.request(
        "POST", suffix, json={**payload, "message": "Changed"}, headers=headers
    )
    assert conflict.status_code == 409
    assert conflict.json["code"] == "idempotency_conflict"
    for table in ("invoice_query_messages", "invoice_query_events", "operator_events"):
        assert before[table][0]["actor"] == {
            "initiating_client_id": http.credential["id"],
            "actor_kind": "agent",
            "agent_label": "Case agent",
        }
    preview = http.request("POST", suffix.replace("false", "true"), json=payload)
    assert preview.status_code == 409
    assert snapshot(http) == before


def test_mcp_case_response(operator_http, monkeypatch):
    import asyncio

    from mcp_sidecar.server import create_app
    from tests.test_mcp_sidecar import session
    from tests.test_mcp_sidecar_postgres import operator_server

    http = operator_http
    ids = helpers.seed_operator_resources(http)
    monkeypatch.setenv("OPENLEG_MCP_ENABLED", "true")
    with operator_server(http.app) as origin:
        monkeypatch.setenv("OPENLEG_OPERATOR_ORIGIN", origin)
        app = create_app()

        async def check():
            async with (
                app.router.lifespan_context(app),
                session(app, http.headers) as sdk,
            ):
                tool = next(
                    t
                    for t in (await sdk.list_tools()).tools
                    if t.name == "respond_invoice_case"
                )
                assert tool.meta["x-openleg-capability"] == "cases.mutate"
                assert tool.meta["x-openleg-operation-id"] == "respondInvoiceCase"
                assert tool.annotations.destructiveHint is True
                assert tool.annotations.idempotentHint is True
                assert tool.annotations.readOnlyHint is False
                args = {
                    "community_id": http.community_id,
                    "case_id": ids["case_id"],
                    "message": "Checked.",
                    "status": "acknowledged",
                    "idempotency_key": "sdk-key",
                    "dry_run": True,
                }
                before = snapshot(http)
                preview = await sdk.call_tool("respond_invoice_case", args)
                assert not preview.isError
                assert preview.structuredContent["dry_run"] is True
                assert snapshot(http) == before
                missing = await sdk.call_tool(
                    "respond_invoice_case",
                    {k: v for k, v in args.items() if k != "idempotency_key"},
                )
                assert missing.isError
                args["dry_run"] = False
                result = await sdk.call_tool("respond_invoice_case", args)
                assert not result.isError
                assert result.structuredContent["status"] == "acknowledged"
                replay = await sdk.call_tool("respond_invoice_case", args)
                assert replay.structuredContent["replayed"] is True
                conflict = await sdk.call_tool(
                    "respond_invoice_case", {**args, "message": "Changed."}
                )
                assert conflict.isError
                assert conflict.structuredContent["code"] == "idempotency_conflict"

        asyncio.run(check())
