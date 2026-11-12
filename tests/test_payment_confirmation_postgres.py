# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest

from tests import operator_contract_helpers
from tests.operator_contract_helpers import seed_operator_resources as seed_base

interest_database = operator_contract_helpers.interest_database
operator_http = operator_contract_helpers.operator_http

pytestmark = pytest.mark.integration


def seed_operator_resources(http):
    ids = seed_base(http)
    # A second issued invoice carries nonempty immutable evidence at insertion.
    with http.db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO invoices(community_id,participant_id,invoice_number,gross_chf,status,
                    policy_snapshot,provenance_snapshot,line_items_snapshot)
                    VALUES (%s,'contract-admin','PAYMENT-2',42.50,'issued',
                    '{"tariff":42}', '{"source":"fixture"}', '[{"chf":42.5}]') RETURNING id""",
            (http.community_id,),
        )
        ids["invoice_id"] = cur.fetchone()["id"]
        cur.execute(
            """INSERT INTO invoice_lifecycle_events(invoice_id,community_id,actor_id,event_type,
                    previous_state,new_state,idempotency_key) VALUES (%s,%s,'contract-admin','delivered',
                    'issued','delivered','fixture-2')""",
            (ids["invoice_id"], http.community_id),
        )
    return ids


def rows(http):
    tables = (
        "invoices",
        "invoice_lifecycle_events",
        "bank_statement_entries",
        "operator_events",
        "operator_webhook_deliveries",
        "operator_action_idempotency",
    )
    with http.db.get_connection() as conn, conn.cursor() as cur:
        result = {}
        for table in tables:
            cur.execute(
                f"SELECT row_to_json(t)::text AS row FROM {table} t ORDER BY row_to_json(t)::text"
            )
            result[table] = [r["row"] for r in cur.fetchall()]
        return result


def confirm(http, ids, query="", **kwargs):
    return http.request(
        "POST",
        f"/payments/matches/{ids['entry_id']}/confirm" + query,
        json=kwargs.pop("json", {"invoice_id": ids["invoice_id"]}),
        headers=kwargs.pop("headers", {"Idempotency-Key": "payment-test"}),
        **kwargs,
    )


def test_dry_run_preserves_full_rows(operator_http):
    ids = seed_operator_resources(operator_http)
    before = rows(operator_http)
    response = confirm(operator_http, ids, "?dry_run=true")
    assert response.status_code == 200
    assert response.json == {
        "schema_version": "operator-api/1",
        "dry_run": True,
        "eligible": True,
        "invoice_id": ids["invoice_id"],
        "lifecycle_state": "paid",
    }
    assert rows(operator_http) == before
    from tests.test_operator_contract import load_contract

    operator_contract_helpers.validate_response(
        load_contract(),
        "/api/operator/v1/communities/{community_id}/payments/matches/{entry_id}/confirm",
        "post",
        response,
    )


@pytest.mark.parametrize(
    "query,body",
    [
        ("?dry_run=True", {}),
        ("?dry_run=1", {}),
        ("?dry_run=", {}),
        ("?dry_run=true&dry_run=true", {}),
        ("?dry_run=true&dry_run=false", {}),
        ("", {"dry_run": False}),
    ],
)
def test_strict_dry_run(operator_http, query, body):
    ids = seed_operator_resources(operator_http)
    before = rows(operator_http)
    response = confirm(
        operator_http, ids, query, json={"invoice_id": ids["invoice_id"], **body}
    )
    assert response.status_code == 400
    assert response.json["code"] == "invalid_request"
    assert rows(operator_http) == before


@pytest.mark.parametrize(
    "change,code,status",
    [
        ("UPDATE bank_statement_entries SET amount=41", "invalid_payment", 409),
        ("UPDATE bank_statement_entries SET is_reversal=true", "invalid_payment", 409),
        (
            "UPDATE bank_statement_entries SET booking_status='PDNG'",
            "invalid_payment",
            409,
        ),
        ("UPDATE bank_statement_entries SET currency='EUR'", "invalid_payment", 409),
        (
            "UPDATE bank_statement_entries SET credit_debit_indicator='DBIT'",
            "invalid_payment",
            409,
        ),
        (
            "INSERT INTO invoice_lifecycle_events(invoice_id,community_id,actor_id,event_type,previous_state,new_state,idempotency_key) SELECT id,community_id,'contract-admin','paid','delivered','paid','fixture-paid' FROM invoices",
            "invalid_payment",
            409,
        ),
        (
            "INSERT INTO invoices(community_id,participant_id,invoice_number,gross_chf,status) VALUES ('other-leg','contract-admin','FOREIGN',42.50,'issued')",
            "invalid_payment",
            409,
        ),
        (
            "UPDATE bank_statement_entries SET community_id='other-leg'",
            "not_found",
            404,
        ),
    ],
)
@pytest.mark.parametrize("query", ["", "?dry_run=true"])
def test_guards(operator_http, change, code, status, query):
    ids = seed_operator_resources(operator_http)
    with operator_http.db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO communities(community_id,name) VALUES ('other-leg','Other')"
        )
        cur.execute(change)
        if change.startswith("INSERT INTO invoices"):
            cur.execute("SELECT id FROM invoices WHERE community_id='other-leg'")
            ids["invoice_id"] = cur.fetchone()["id"]
            cur.execute(
                """INSERT INTO invoice_lifecycle_events(invoice_id,community_id,actor_id,event_type,
                        previous_state,new_state,idempotency_key) VALUES (%s,'other-leg','contract-admin',
                        'delivered','issued','delivered','foreign-delivery')""",
                (ids["invoice_id"],),
            )
    before = rows(operator_http)
    response = confirm(operator_http, ids, query)
    assert response.status_code == status
    assert response.json["code"] == code
    assert rows(operator_http) == before


def test_confirmation_replay_snapshot_and_actor(operator_http):
    http = operator_http
    ids = seed_operator_resources(http)
    with http.db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE operator_api_clients SET actor_kind='agent',agent_label='Payment agent'"
        )
    before = rows(http)
    missing = confirm(http, ids, headers={})
    assert missing.status_code == 400
    assert missing.json["code"] == "invalid_idempotency_key"
    first = confirm(http, ids, "?dry_run=false")
    assert first.status_code == 200
    assert first.json["replayed"] is False
    after = rows(http)
    assert after["invoices"] == before["invoices"]
    assert confirm(http, ids).json == {**first.json, "replayed": True}
    assert rows(http) == after
    conflict = confirm(http, ids, json={"invoice_id": ids["invoice_id"] + 1})
    assert conflict.status_code == 409
    assert conflict.json["code"] == "idempotency_conflict"
    import json

    actor = {
        "initiating_client_id": http.credential["id"],
        "actor_kind": "agent",
        "agent_label": "Payment agent",
    }
    transitions = [json.loads(row) for row in after["invoice_lifecycle_events"]]
    paid = [row for row in transitions if row["event_type"] == "paid"]
    assert len(paid) == 1
    assert paid[0]["actor"] == actor
    assert paid[0]["new_state"] == "paid"
    events = [json.loads(row) for row in after["operator_events"]]
    for event_type in ("invoice.paid", "payment.match.confirmed"):
        matches = [row for row in events if row["event_type"] == event_type]
        assert len(matches) == 1
        assert matches[0]["actor"] == actor


def test_mcp_payment_tool(operator_http, monkeypatch):
    import asyncio

    from mcp_sidecar.server import create_app
    from tests.test_mcp_sidecar import session
    from tests.test_mcp_sidecar_postgres import operator_server

    http = operator_http
    ids = seed_operator_resources(http)
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
                    if t.name == "confirm_payment_match"
                )
                assert tool.meta["x-openleg-capability"] == "payments.mutate"
                assert tool.meta["operationId"] == "confirmPaymentMatch"
                assert tool.annotations.readOnlyHint is False
                assert tool.annotations.destructiveHint is True
                assert tool.annotations.idempotentHint is True
                args = {
                    "community_id": http.community_id,
                    "entry_id": ids["entry_id"],
                    "invoice_id": ids["invoice_id"],
                    "idempotency_key": "mcp-payment",
                    "dry_run": True,
                }
                before = rows(http)
                preview = await sdk.call_tool(tool.name, args)
                assert not preview.isError
                assert preview.structuredContent["eligible"] is True
                assert rows(http) == before
                foreign = await sdk.call_tool(
                    tool.name, {**args, "community_id": "foreign-leg"}
                )
                assert foreign.isError
                assert foreign.structuredContent["code"] == "not_found"
                assert rows(http) == before
                invalid = await sdk.call_tool(
                    tool.name, {k: v for k, v in args.items() if k != "idempotency_key"}
                )
                assert invalid.isError
                result = await sdk.call_tool(tool.name, {**args, "dry_run": False})
                assert not result.isError
                assert result.structuredContent["match_decision"] == "matched"
                replay = await sdk.call_tool(tool.name, {**args, "dry_run": False})
                assert replay.structuredContent["replayed"] is True

        asyncio.run(check())


@pytest.fixture
def paused_confirmation(operator_http, monkeypatch):
    """Pause one HTTP request during real eligibility validation, holding its transaction."""
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    from threading import Event, current_thread

    import payment_reconciliation

    http = operator_http
    original_connection = http.db.get_connection
    original_eligibility = payment_reconciliation.payment_eligibility
    main_thread = current_thread()
    entered, release = Event(), Event()

    @contextmanager
    def connection():
        with original_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL lock_timeout = '200ms'")
            yield conn

    def eligibility(*args):
        if current_thread() is not main_thread:
            entered.set()
            assert release.wait(10), "paused confirmation was not released"
        return original_eligibility(*args)

    monkeypatch.setattr(http.db, "get_connection", connection)
    monkeypatch.setattr(payment_reconciliation, "payment_eligibility", eligibility)

    @contextmanager
    def pause(ids, *, dry_run):
        def request():
            with http.app.test_client() as client:
                return client.post(
                    f"/api/operator/v1/communities/{http.community_id}/payments/matches/{ids['entry_id']}/confirm",
                    query_string={"dry_run": str(dry_run).lower()},
                    headers={**http.headers, "Idempotency-Key": "paused"},
                    json={"invoice_id": ids["invoice_id"]},
                )

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(request)
            try:
                assert entered.wait(5), "confirmation did not reach validation"
                yield future
            finally:
                release.set()
            assert future.result(timeout=5).status_code == 200

    return pause


@pytest.mark.parametrize("key", ["different", "paused"])
def test_paused_preview_does_not_block_confirmation(
    operator_http, paused_confirmation, key
):
    ids = seed_operator_resources(operator_http)
    with paused_confirmation(ids, dry_run=True) as preview:
        response = confirm(operator_http, ids, headers={"Idempotency-Key": key})
        assert response.status_code == 200
        assert response.json["match_decision"] == "matched"
    assert preview.result().json["eligible"] is True


@pytest.mark.parametrize("same_entry", [True, False])
def test_real_confirmation_keeps_payment_locks(
    operator_http, paused_confirmation, same_entry
):
    from psycopg2.errors import LockNotAvailable

    http = operator_http
    ids = seed_operator_resources(http)
    contender = dict(ids)
    if not same_entry:
        with http.db.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO bank_statement_entries(
                       statement_import_id,community_id,entry_reference,booking_date,
                       amount,currency,payment_reference,credit_debit_indicator,
                       match_decision,booking_status)
                   SELECT statement_import_id,community_id,'ENTRY-2',booking_date,
                          amount,currency,payment_reference,credit_debit_indicator,
                          match_decision,booking_status
                   FROM bank_statement_entries WHERE id=%s RETURNING id""",
                (ids["entry_id"],),
            )
            contender["entry_id"] = cur.fetchone()["id"]
    with paused_confirmation(ids, dry_run=False):
        with pytest.raises(LockNotAvailable, match="lock timeout"):
            confirm(http, contender, headers={"Idempotency-Key": "contender"})
    rejected = confirm(http, contender, headers={"Idempotency-Key": "contender"})
    assert rejected.status_code == (404 if same_entry else 409)
    with http.db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM invoice_lifecycle_events WHERE event_type='paid'"
        )
        assert cur.fetchone()["n"] == 1
