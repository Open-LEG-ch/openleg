# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
from contextlib import contextmanager
from threading import Thread

import pytest
from werkzeug.serving import make_server

from tests import operator_contract_helpers

interest_database = operator_contract_helpers.interest_database
operator_http = operator_contract_helpers.operator_http
from tests.test_mcp_sidecar import session


@contextmanager
def operator_server(app):
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()


@pytest.mark.integration
def test_real_http_pagination_and_scope(operator_http, monkeypatch, caplog):
    from mcp_sidecar.server import create_app

    fixture = operator_http
    with fixture.db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO communities(community_id,name) VALUES ('other-leg','Other fictional LEG')"
        )
        cur.execute(
            "INSERT INTO billing_periods(community_id,period_start,period_end) VALUES ('other-leg','2026-01-01','2026-02-01')"
        )
        for month in (1, 2):
            cur.execute(
                "INSERT INTO billing_periods(community_id,period_start,period_end) VALUES (%s,%s,%s)",
                (
                    fixture.community_id,
                    f"2026-{month:02d}-01",
                    f"2026-{month + 1:02d}-01",
                ),
            )
    monkeypatch.setenv("OPENLEG_MCP_ENABLED", "true")
    with operator_server(fixture.app) as origin:
        monkeypatch.setenv("OPENLEG_OPERATOR_ORIGIN", origin)
        app = create_app()

        async def check():
            async with (
                app.router.lifespan_context(app),
                session(app, fixture.headers, expose_http=True) as (sdk, transport),
            ):
                args = {"community_id": fixture.community_id, "limit": 1}
                first = await sdk.call_tool("list_billing_periods", args)
                assert not first.isError
                expected = fixture.request("GET", "/billing/periods?limit=1").get_json()
                assert first.structuredContent == {
                    k: expected[k] for k in ("items", "next_cursor")
                }
                assert first.structuredContent["next_cursor"] is not None
                args["cursor"] = first.structuredContent["next_cursor"]
                second = await sdk.call_tool("list_billing_periods", args)
                assert not second.isError
                assert (
                    second.structuredContent["items"]
                    != first.structuredContent["items"]
                )
                assert second.structuredContent["next_cursor"] is None
                transport.headers.pop("Authorization")
                missing = await sdk.call_tool("list_billing_periods", args)
                assert missing.isError
                assert missing.content[0].text == "unauthorized"
                transport.headers.update(fixture.headers)
                denied = await sdk.call_tool(
                    "list_billing_periods", {"community_id": "other-leg"}
                )
                assert denied.isError
                assert denied.content[0].text == "not_found"
                async with session(app) as anonymous:
                    missing = await anonymous.call_tool("list_billing_periods", args)
                    assert missing.isError
                    assert missing.content[0].text == "unauthorized"
                restricted, _ = fixture.credential_headers(["payments.read"])
                async with session(app, restricted) as other:
                    denied = await other.call_tool("list_billing_periods", args)
                    assert denied.isError
                    assert denied.content[0].text == "forbidden"
                assert (
                    fixture.headers["Authorization"].split()[1]
                    not in str(first) + str(denied) + caplog.text
                )

        asyncio.run(check())
