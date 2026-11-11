"""HTTP previews preserve all business rows, including existing cases."""

import pytest

import vnb_exchange
from tests import operator_contract_helpers as helpers

interest_database = helpers.interest_database
operator_http = helpers.operator_http
pytestmark = pytest.mark.integration

MEMBERSHIP = {
    "mutation_id": "preview-change",
    "participant_id": "contract-admin",
    "mutation_type": "change",
    "effective_date": "2026-12-01",
    "source_agreement_id": "agreement",
    "after": {"role": "member"},
}
SCENARIOS = [("/formation/submissions", {}), ("/membership-mutations", MEMBERSHIP)]


def snapshot(db):
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")
        result = {}
        for table in [row["tablename"] for row in cur.fetchall()]:
            cur.execute(
                f'SELECT to_jsonb(t) AS row FROM "{table}" t ORDER BY to_jsonb(t)::text'
            )
            result[table] = [row["row"] for row in cur.fetchall()]
        return result


@pytest.mark.parametrize("suffix,payload", SCENARIOS)
def test_preview_only_accounts_usage(operator_http, monkeypatch, suffix, payload):
    http = operator_http
    ids = helpers.seed_operator_resources(http)
    with http.db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE operator_api_clients SET webhook_url='https://example.invalid/events'"
        )
    seeded = http.request(
        "POST",
        f"/billing/cases/{ids['case_id']}/responses",
        json={"message": "Existing evidence", "status": "acknowledged"},
        headers={"Idempotency-Key": "existing-evidence"},
    )
    assert seeded.status_code == 200
    before = snapshot(http.db)
    assert before["operator_events"]
    assert before["operator_webhook_deliveries"]
    assert before["operator_action_idempotency"]

    def forbidden(*args, **kwargs):
        pytest.fail("Preview built a package or called transport")

    monkeypatch.setattr(vnb_exchange, "_manual_package", forbidden)
    monkeypatch.setattr(vnb_exchange, "_manual_mutation_package", forbidden)
    monkeypatch.setattr(vnb_exchange.ManualHandoverAdapter, "submit", forbidden)
    response = http.request(
        "POST", suffix, json=payload, query_string={"dry_run": "true"}
    )
    assert response.status_code == 200, response.json
    assert response.json["intended_action"] == "prepare_manual_package"
    assert response.json["recipient"] == "manual / not configured"
    assert len(response.json["payload_fingerprint"]) == 64
    after = snapshot(http.db)
    assert {t for t in before if before[t] != after[t]} == {
        "operator_api_clients",
        "operator_api_usage",
    }
    clients_before, clients_after = (
        before.pop("operator_api_clients"),
        after.pop("operator_api_clients"),
    )
    for rows in (clients_before, clients_after):
        for row in rows:
            row.pop("last_used_at")
    assert clients_before == clients_after
    usage_before = before.pop("operator_api_usage")
    usage_after = after.pop("operator_api_usage")
    assert len(usage_after) == len(usage_before) + 1
    assert all(row in usage_after for row in usage_before)
    assert before == after


@pytest.mark.parametrize("suffix,payload", SCENARIOS)
def test_existing_case_is_never_claimed(operator_http, suffix, payload):
    http = operator_http
    helpers.seed_operator_resources(http)
    assert http.request("POST", suffix, json=payload).status_code == 202
    before = snapshot(http.db)
    response = http.request(
        "POST", suffix, json=payload, query_string={"dry_run": "true"}
    )
    assert response.status_code == 200
    table = (
        "vnb_submission_cases"
        if suffix == "/formation/submissions"
        else "vnb_mutation_cases"
    )
    assert (
        response.json["payload_fingerprint"] == before[table][0]["payload_fingerprint"]
    )
    after = snapshot(http.db)
    for table in before:
        if table not in {"operator_api_clients", "operator_api_usage"}:
            assert before[table] == after[table], table
    # Different content/identity must conflict with the existing case.
    if suffix == "/formation/submissions":
        with http.db.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE leg_documents SET pdf_data=%s", (b"changed signed bytes",)
            )
        conflict_payload = payload
    else:
        conflict_payload = {**payload, "mutation_id": "other"}
    before = snapshot(http.db)
    preview = http.request(
        "POST", suffix, json=conflict_payload, query_string={"dry_run": "true"}
    )
    after = snapshot(http.db)
    for table in before:
        if table not in {"operator_api_clients", "operator_api_usage"}:
            assert before[table] == after[table], table
    real = http.request("POST", suffix, json=conflict_payload)
    assert preview.status_code == real.status_code == 409
    assert preview.json["code"] == real.json["code"] == "conflict"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {**MEMBERSHIP, "effective_date": "bad"},
        {**MEMBERSHIP, "participant_id": "foreign"},
        {**MEMBERSHIP, "after": {"metering_point_id": "secret"}},
        {**MEMBERSHIP, "mutation_type": "join"},
        {**MEMBERSHIP, "mutation_id": ""},
    ],
)
def test_validation_codes_match(operator_http, payload):
    for dry in ("true", "false"):
        response = operator_http.request(
            "POST", "/membership-mutations", json=payload, query_string={"dry_run": dry}
        )
        assert response.status_code == 409
        assert response.json["code"] == "invalid_membership"


def test_invalid_formation_code_matches(operator_http):
    for dry in ("true", "false"):
        response = operator_http.request(
            "POST", "/formation/submissions", query_string={"dry_run": dry}
        )
        assert response.status_code == 409
        assert response.json["code"] == "invalid_formation"


@pytest.mark.parametrize("suffix,payload", SCENARIOS)
def test_automated_preview_describes_recipient_without_transport(
    operator_http, monkeypatch, suffix, payload
):
    class Adapter:
        def contract(self):
            return vnb_exchange.ExchangeContract(
                "test",
                "vnb-formation/1",
                frozenset({"formation_submission", "participant_change"}),
                "Fictional VNB endpoint",
            )

        def submit(self, *args, **kwargs):
            pytest.fail("Transport called")

        submit_mutation = submit

    monkeypatch.setitem(vnb_exchange.ADAPTERS, "test", Adapter())
    http = operator_http
    helpers.seed_operator_resources(http)
    with http.db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE communities SET vnb_adapter_key='test'")
    response = http.request(
        "POST", suffix, json=payload, query_string={"dry_run": "true"}
    )
    assert response.status_code == 200
    assert response.json["intended_action"] == "send_through_adapter"
    assert response.json["recipient"] == "Fictional VNB endpoint"
    from tests.test_operator_contract import load_contract

    helpers.validate_response(
        load_contract(),
        "/api/operator/v1/communities/{community_id}" + suffix,
        "post",
        response,
    )


@pytest.mark.parametrize("suffix,payload", SCENARIOS)
def test_mcp_preview_cannot_submit(operator_http, monkeypatch, suffix, payload):
    import asyncio

    from mcp_sidecar.server import create_app
    from tests.test_mcp_sidecar import session
    from tests.test_mcp_sidecar_postgres import operator_server

    http = operator_http
    helpers.seed_operator_resources(http)
    before = snapshot(http.db)
    monkeypatch.setenv("OPENLEG_MCP_ENABLED", "true")
    with operator_server(http.app) as origin:
        monkeypatch.setenv("OPENLEG_OPERATOR_ORIGIN", origin)
        app = create_app()

        async def check():
            async with (
                app.router.lifespan_context(app),
                session(app, http.headers) as sdk,
            ):
                name = (
                    "preview_formation_submission"
                    if suffix == "/formation/submissions"
                    else "preview_membership_mutation"
                )
                args = {"community_id": http.community_id, **payload}
                result = await sdk.call_tool(name, args)
                assert not result.isError
                assert (
                    result.structuredContent["intended_action"]
                    == "prepare_manual_package"
                )
                assert (await sdk.call_tool(name, {**args, "dry_run": False})).isError
                assert (await sdk.call_tool("submit_formation", args)).isError

        asyncio.run(check())
    after = snapshot(http.db)
    for table in before:
        if table not in {"operator_api_clients", "operator_api_usage"}:
            assert before[table] == after[table], table


@pytest.mark.parametrize("suffix,payload", SCENARIOS)
def test_preview_security_matches_real_call(operator_http, suffix, payload):
    http = operator_http
    denied, _ = http.credential_headers([])
    limited, _ = http.credential_headers(
        ["formation.mutate", "membership.mutate"], budget=1
    )
    for dry in ("true", "false"):
        for headers, cid, code in [
            ({}, http.community_id, "authentication_required"),
            (denied, http.community_id, "capability_denied"),
            (http.headers, "foreign", "not_found"),
        ]:
            response = http.client.post(
                "/api/operator/v1/communities/" + cid + suffix,
                json=payload,
                headers=headers,
                query_string={"dry_run": dry},
            )
            assert response.json["code"] == code
    http.request(
        "POST", suffix, json=payload, headers=limited, query_string={"dry_run": "true"}
    )
    response = http.request(
        "POST", suffix, json=payload, headers=limited, query_string={"dry_run": "true"}
    )
    assert response.status_code == 429


@pytest.mark.parametrize("state", ["failed", "claimed"])
@pytest.mark.parametrize("suffix,payload", SCENARIOS)
def test_retryable_case_content_is_unchanged(operator_http, suffix, payload, state):
    http = operator_http
    helpers.seed_operator_resources(http)
    assert http.request("POST", suffix, json=payload).status_code == 202
    table = (
        "vnb_submission_cases"
        if suffix == "/formation/submissions"
        else "vnb_mutation_cases"
    )
    with http.db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE {table} SET state=%s, retryable=true, next_action='retry'",
            (state,),
        )
    before = snapshot(http.db)
    response = http.request(
        "POST", suffix, json=payload, query_string={"dry_run": "true"}
    )
    assert response.status_code == 200
    table = (
        "vnb_submission_cases"
        if suffix == "/formation/submissions"
        else "vnb_mutation_cases"
    )
    assert (
        response.json["payload_fingerprint"] == before[table][0]["payload_fingerprint"]
    )
    after = snapshot(http.db)
    for table in before:
        if table not in {"operator_api_clients", "operator_api_usage"}:
            assert before[table] == after[table], table


def test_membership_replay_preview_keeps_real_call_validation(
    operator_http, monkeypatch
):
    http = operator_http
    assert (
        http.request("POST", "/membership-mutations", json=MEMBERSHIP).status_code
        == 202
    )
    # Completed/prepared replays do not require the historical adapter to remain installed.
    monkeypatch.delitem(vnb_exchange.ADAPTERS, "manual-handover")
    real = http.request("POST", "/membership-mutations", json=MEMBERSHIP)
    preview = http.request(
        "POST",
        "/membership-mutations",
        json=MEMBERSHIP,
        query_string={"dry_run": "true"},
    )
    assert real.status_code == 202
    assert preview.status_code == 200, preview.json
