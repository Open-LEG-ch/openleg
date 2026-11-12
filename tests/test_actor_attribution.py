# SPDX-License-Identifier: AGPL-3.0-or-later
"""Operator actor attribution at HTTP and PostgreSQL seams."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask

import operator_api
from tests import test_interest_postgres
from tests import test_payment_acceptance_postgres as payments
from tests.test_operator_api import CLIENT

interest_database = test_interest_postgres.interest_database
billing_database = payments.billing_database
payment_client = payments.payment_client


@pytest.mark.parametrize(
    "path,message",
    [
        ("formation/submissions", "Formation submission denied"),
        ("membership-mutations", "Membership mutation denied"),
    ],
)
def test_agent_cannot_reach_vnb(path, message):
    app = Flask(__name__)
    app.register_blueprint(operator_api.operator_api_bp)
    with (
        patch.object(
            operator_api.db,
            "get_operator_api_client_by_token_hash",
            return_value={**CLIENT, "actor_kind": "agent"},
        ),
        patch.object(operator_api.db, "claim_operator_api_usage", return_value=True),
        patch.object(
            operator_api.db,
            "create_agent_confirmation_request",
            return_value={
                "state": "awaiting_confirmation",
                "request_id": "pending",
                "payload_fingerprint": "f" * 64,
            },
        ),
        patch.object(operator_api.vnb_exchange, "submit_formation") as formation,
        patch.object(
            operator_api.vnb_exchange, "submit_membership_mutation"
        ) as membership,
    ):
        formation.return_value = membership.return_value = SimpleNamespace(
            state="prepared", case_id="case", event_id="event"
        )
        response = app.test_client().post(
            "/api/operator/v1/communities/community-a/" + path,
            headers={"Authorization": "Bearer secret"},
            json={},
        )
    assert response.status_code == 202
    assert response.json["state"] == "awaiting_confirmation"
    for call in formation.call_args_list + membership.call_args_list:
        assert call.kwargs == {"dry_run": True}


@pytest.mark.integration
def test_legacy_credentials_migrate_to_service(interest_database):
    db = interest_database
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("ALTER TABLE operator_api_clients DROP COLUMN IF EXISTS actor_kind")
        cur.execute(
            "ALTER TABLE operator_api_clients DROP COLUMN IF EXISTS agent_label"
        )
        cur.execute(
            "INSERT INTO communities(community_id,name) VALUES ('leg','Fictional LEG')"
        )
        cur.execute(
            "INSERT INTO operator_api_clients(id,community_id,created_by,name,capabilities,token_hash) VALUES ('old','leg','building','Legacy','[]','hash')"
        )
    db.create_tables()
    db.create_tables()
    row = db.list_operator_api_clients("leg")[0]
    assert row["actor_kind"] == "service"
    assert row["agent_label"] is None


@pytest.mark.integration
def test_dashboard_requires_actor_choice_and_stores_it(interest_database):
    db = interest_database
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO communities(community_id,name) VALUES ('leg','Fictional LEG')"
        )
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(operator_api.operator_api_bp)
    client = app.test_client()
    with client.session_transaction() as session:
        session["dashboard_building_id"] = "building"
        session["dashboard_csrf_token"] = "csrf"
    payload = {"name": "Assistant", "capabilities": ["cases.mutate"]}
    with patch.object(operator_api, "_admin_for", return_value=True):
        response = client.post(
            "/leg/community/leg/operator-api/credentials",
            json=payload,
            headers={"X-CSRF-Token": "csrf"},
        )
        assert response.status_code == 400
        response = client.post(
            "/leg/community/leg/operator-api/credentials",
            json={**payload, "actor_kind": "agent", "agent_label": "LEA"},
            headers={"X-CSRF-Token": "csrf"},
        )
    assert response.status_code == 201
    row = db.get_operator_api_client_by_token_hash(
        operator_api._token_hash(response.json["token"])
    )
    assert row["created_by"] == "building"
    assert row["actor_kind"] == "agent"
    assert response.json["credential"]["agent_label"] == "LEA"


@pytest.mark.integration
@pytest.mark.parametrize("kind", ["human", "service", "agent"])
def test_http_membership_attribution_survives_credential_changes(
    interest_database, kind
):
    import vnb_exchange
    from store import vnb_exchange as store

    db = interest_database
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO communities(community_id,name) VALUES ('leg','Fictional LEG')"
        )
    sender = db.create_operator_api_client(
        "leg",
        "building",
        "Sender",
        ["membership.mutate"],
        operator_api._token_hash("token"),
        None,
        actor_kind=kind,
        agent_label="Import",
    )
    subscriber = db.create_operator_api_client(
        "leg",
        "building",
        "Subscriber",
        ["membership.read"],
        "other",
        "https://example.org",
    )
    app = Flask(__name__)
    app.register_blueprint(operator_api.operator_api_bp)

    def submit(command, *, dry_run=False):
        assert command.actor_building_id == "building"
        if dry_run:
            return vnb_exchange.SubmissionPreview(
                "prepare_manual_package", "f" * 64, "manual", {}, "manual"
            )
        row = store.claim_mutation(
            vnb_exchange.MutationClaim(
                "mutation",
                "leg",
                "participant",
                command.actor_building_id,
                "join",
                "2026-11-11",
                "agreement",
                {},
                {},
                "manual",
                "vnb-membership/1",
                (),
                "f" * 64,
            )
        )
        row = store.record_mutation_outcome(
            row["case_id"],
            vnb_exchange.MutationOutcome(
                case_id=row["case_id"],
                contract_version="vnb-membership/1",
                payload_fingerprint="f" * 64,
                state="prepared",
                next_action="download_and_deliver",
                manual_package=b"package",
            ),
        )
        return SimpleNamespace(**row)

    with patch.object(vnb_exchange, "submit_membership_mutation", side_effect=submit):
        response = app.test_client().post(
            "/api/operator/v1/communities/leg/membership-mutations",
            headers={"Authorization": "Bearer token"},
            json={},
        )
    if kind == "agent":
        assert response.status_code == 202
        assert response.json["state"] == "awaiting_confirmation"
        with db.get_connection() as conn, conn.cursor() as cur:
            for table in (
                "vnb_mutation_cases",
                "vnb_mutation_events",
                "operator_events",
                "operator_webhook_deliveries",
            ):
                cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
                assert cur.fetchone()["n"] == 0
        return
    assert response.status_code == 202
    expected = {
        "initiating_client_id": sender["id"],
        "actor_kind": kind,
        "agent_label": "Import",
    }
    assert db.list_vnb_mutations("leg")[0]["actor"] == expected
    db.rotate_operator_api_client("leg", sender["id"], "new")
    db.revoke_operator_api_client("leg", sender["id"])
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE operator_api_clients SET agent_label='Changed' WHERE id=%s",
            (sender["id"],),
        )
        cur.execute(
            "SELECT e.actor,d.client_id FROM operator_events e JOIN operator_webhook_deliveries d USING(event_id)"
        )
        row = cur.fetchone()
        assert row["actor"] == expected
        assert row["client_id"] == subscriber["id"]
    assert db.list_vnb_mutations("leg")[0]["actor"] == expected
    assert db.list_operator_webhook_deliveries("leg")[0]["actor"] == expected


@pytest.mark.integration
def test_agent_invoice_reply_records_actor_on_message_and_events(interest_database):
    db = interest_database
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO communities(community_id,name) VALUES ('leg','Fictional LEG')"
        )
        cur.execute("INSERT INTO invoices(community_id) VALUES ('leg') RETURNING id")
        invoice = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO invoice_queries(invoice_id,community_id,participant_id,category) VALUES (%s,'leg','member','other') RETURNING id",
            (invoice,),
        )
        case_id = cur.fetchone()["id"]
    actor = db.create_operator_api_client(
        "leg",
        "building",
        "Agent",
        ["cases.mutate"],
        operator_api._token_hash("token"),
        None,
        actor_kind="agent",
        agent_label="LEA",
    )
    app = Flask(__name__)
    app.register_blueprint(operator_api.operator_api_bp)
    response = app.test_client().post(
        f"/api/operator/v1/communities/leg/billing/cases/{case_id}/responses",
        headers={"Authorization": "Bearer token", "Idempotency-Key": "reply"},
        json={"message": "Wir prüfen die Rechnung.", "status": "acknowledged"},
    )
    assert response.status_code == 200
    expected = {
        "initiating_client_id": actor["id"],
        "actor_kind": "agent",
        "agent_label": "LEA",
    }
    with db.get_connection() as conn, conn.cursor() as cur:
        for table in (
            "invoice_query_messages",
            "invoice_query_events",
            "operator_events",
        ):
            cur.execute(f"SELECT actor FROM {table}")
            assert cur.fetchone()["actor"] == expected


@pytest.mark.integration
def test_payment_records_initiator_on_lifecycle_and_operator_events(payment_client):
    import database as db

    invoice = payments._invoice()
    entry, _ = payments._import()
    response = payments._confirm(payment_client, entry, invoice)
    assert response.status_code == 200
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM operator_api_clients WHERE name='Test operator'")
        initiator = cur.fetchone()["id"]
        cur.execute(
            "SELECT actor FROM invoice_lifecycle_events WHERE event_type='paid'"
        )
        assert cur.fetchone()["actor"]["initiating_client_id"] == initiator
        cur.execute(
            "SELECT actor FROM operator_events WHERE event_type IN ('invoice.paid','payment.match.confirmed')"
        )
        assert all(
            row["actor"]["initiating_client_id"] == initiator for row in cur.fetchall()
        )


@pytest.mark.integration
def test_metering_retry_records_initiator_on_run(interest_database):
    from datetime import UTC, datetime

    db = interest_database
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO communities(community_id,name) VALUES ('community-a','Fictional LEG')"
        )
        cur.execute("INSERT INTO white_label_configs(territory) VALUES ('test')")
    app = Flask(__name__)
    app.register_blueprint(operator_api.operator_api_bp)
    report = {
        "started_at": datetime.now(UTC),
        "finished_at": datetime.now(UTC),
        "status": "success",
        "attempts": 1,
        "downloaded_files": 0,
        "imported_files": 0,
        "imported_readings": 0,
    }

    def run(*args):
        db.record_sdat_ingestion_run("test", report)
        return report

    with (
        patch.object(
            db,
            "get_operator_api_client_by_token_hash",
            return_value={**CLIENT, "capabilities": ["metering.mutate"]},
        ),
        patch.object(db, "claim_operator_api_usage", return_value=True),
        patch.object(
            db,
            "claim_operator_ingestion_retry",
            return_value={"schedule": {"territory": "test"}},
        ),
        patch.object(operator_api.sdat_ingestion, "run", side_effect=run),
    ):
        response = app.test_client().post(
            "/api/operator/v1/communities/community-a/metering/jobs/1/retry",
            headers={"Authorization": "Bearer token", "Idempotency-Key": "retry"},
        )
    assert response.status_code == 200
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT actor FROM sdat_ingestion_runs")
        assert cur.fetchone()["actor"]["initiating_client_id"] == CLIENT["id"]


def test_operator_console_requires_actor_choice_and_displays_history():
    from pathlib import Path

    from jinja2 import Environment, FileSystemLoader

    env = Environment(loader=FileSystemLoader(Path("templates")), autoescape=True)
    env.globals["_"] = lambda text: text
    # Render fragments independently of the product shell's app helpers.
    actor = {
        "initiating_client_id": "client",
        "actor_kind": "agent",
        "agent_label": "<LEA>",
    }
    template = env.get_template("partials/operator_actor.html")
    rendered = template.render(actor=actor)
    assert "Agent" in rendered and "&lt;LEA&gt;" in rendered
    source = Path("templates/sandbox/operator.html").read_text()
    assert 'name="actor_kind"' in source
    assert 'name="agent_label"' in source


@pytest.mark.integration
@pytest.mark.parametrize("kind", ["service", "agent"])
def test_formation_gate_prevents_package_and_event_creation(interest_database, kind):
    import vnb_exchange
    from store import vnb_exchange as store

    db = interest_database
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO communities(community_id,name) VALUES ('leg','Fictional LEG')"
        )
    actor = db.create_operator_api_client(
        "leg",
        "building",
        "Sender",
        ["formation.mutate"],
        operator_api._token_hash("token"),
        None,
        actor_kind=kind,
    )
    app = Flask(__name__)
    app.register_blueprint(operator_api.operator_api_bp)

    def submit(command, *, dry_run=False):
        assert command.actor_building_id == "building"
        if dry_run:
            return vnb_exchange.SubmissionPreview(
                "prepare_manual_package", "f" * 64, "manual", {}, "manual"
            )
        with patch.object(store, "_guard_current_formation_bundle", return_value=None):
            row = store.claim(
                vnb_exchange.SubmissionClaim(
                    "leg",
                    command.actor_building_id,
                    "manual",
                    "vnb-formation/1",
                    (),
                    "f" * 64,
                )
            )
        return SimpleNamespace(
            **store.record_outcome(
                row["case_id"],
                vnb_exchange.SubmissionOutcome(
                    case_id=row["case_id"],
                    contract_version="vnb-formation/1",
                    payload_fingerprint="f" * 64,
                    state="prepared",
                    next_action="download_and_deliver",
                    manual_package=b"package",
                ),
            )
        )

    with patch.object(vnb_exchange, "submit_formation", side_effect=submit):
        response = app.test_client().post(
            "/api/operator/v1/communities/leg/formation/submissions",
            headers={"Authorization": "Bearer token"},
        )
    assert response.status_code == 202
    if kind == "agent":
        assert response.json["state"] == "awaiting_confirmation"
    with db.get_connection() as conn, conn.cursor() as cur:
        for table in ("vnb_submission_cases", "operator_events"):
            cur.execute(f"SELECT * FROM {table}")
            rows = cur.fetchall()
            if kind == "agent":
                assert rows == []
            else:
                assert len(rows) == 1
                assert rows[0]["actor"]["initiating_client_id"] == actor["id"]
    import operator_actor

    assert operator_actor.snapshot() is None


def test_webhook_actor_is_optional_and_never_uses_subscriber_identity():
    import json

    from tests.test_operator_api import _delivery_row

    actor = {
        "initiating_client_id": "initiator",
        "actor_kind": "agent",
        "agent_label": "LEA",
    }
    bodies = []

    def transport(url, body, headers, timeout):
        assert operator_api.verify_webhook_signature(
            body, headers["OpenLEG-Signature"], "stored-secret"
        )
        bodies.append(json.loads(body))
        return 200

    with (
        patch.object(
            operator_api.db,
            "get_pending_webhook_deliveries",
            return_value=[
                _delivery_row(
                    actor=actor, client_id="subscriber", webhook_secret="stored-secret"
                )
            ],
        ),
        patch.object(operator_api.db, "record_webhook_attempt"),
    ):
        operator_api.dispatch_pending_webhooks(transport=transport)
    assert bodies[0]["actor"] == actor
    assert bodies[0]["schema_version"] == "operator-event/1"
    with (
        patch.object(
            operator_api.db,
            "get_pending_webhook_deliveries",
            return_value=[_delivery_row(webhook_secret="stored-secret")],
        ),
        patch.object(operator_api.db, "record_webhook_attempt"),
    ):
        operator_api.dispatch_pending_webhooks(transport=transport)
    assert "actor" not in bodies[1]


@pytest.mark.integration
def test_membership_store_retains_agent_identity_for_future_confirmation(
    interest_database,
):
    import operator_actor
    import vnb_exchange
    from store import vnb_exchange as store

    db = interest_database
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO communities(community_id,name) VALUES ('leg','Fictional LEG')"
        )
    actor = db.create_operator_api_client(
        "leg",
        "building",
        "Agent",
        ["membership.mutate"],
        "hash",
        None,
        actor_kind="agent",
        agent_label="LEA",
    )
    with operator_actor.acting_as(actor):
        row = store.claim_mutation(
            vnb_exchange.MutationClaim(
                "mutation",
                "leg",
                "member",
                "building",
                "join",
                "2026-11-11",
                "agreement",
                {},
                {},
                "manual",
                "vnb-membership/1",
                (),
                "f" * 64,
            )
        )
    # Completion may occur after the initiating HTTP request. Use the saved
    # identity, never a current credential or the completing worker's context.
    store.record_mutation_outcome(
        row["case_id"],
        vnb_exchange.MutationOutcome(
            case_id=row["case_id"],
            contract_version="vnb-membership/1",
            payload_fingerprint="f" * 64,
            state="prepared",
            next_action="download_and_deliver",
            manual_package=b"package",
        ),
    )
    expected = {
        "initiating_client_id": actor["id"],
        "actor_kind": "agent",
        "agent_label": "LEA",
    }
    assert db.list_vnb_mutations("leg")[0]["actor"] == expected
    with db.get_connection() as conn, conn.cursor() as cur:
        for table in ("vnb_mutation_events", "operator_events"):
            cur.execute(f"SELECT actor FROM {table}")
            assert cur.fetchone()["actor"] == expected
