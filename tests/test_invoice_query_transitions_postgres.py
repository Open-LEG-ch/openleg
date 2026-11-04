# SPDX-License-Identifier: AGPL-3.0-or-later
"""Durable invoice-question transitions at both real adapter seams."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

import database as db
from tests import test_store_billing_postgres
from tests.test_store_billing_postgres import COMMUNITY, MEMBER, _insert_issued_invoice

billing_database = test_store_billing_postgres.billing_database
pytestmark = pytest.mark.integration


@pytest.fixture
def invoice_case(billing_database):
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO communities(community_id,name) VALUES (%s,'Fictional LEG')",
            (COMMUNITY,),
        )
    db.create_operator_api_client(
        COMMUNITY,
        "operator",
        "Case subscriber",
        ["cases.read"],
        hashlib.sha256(b"fictional-cases").hexdigest(),
        "https://receiver.example.org/events",
    )
    invoice_id = _insert_issued_invoice("LEG-QUESTION-1")
    return db.open_invoice_query(invoice_id, MEMBER, "amount", "Fictional question")


def test_successive_dashboard_changes_deliver_distinct_status_events(invoice_case):
    for status in ("acknowledged", "resolved"):
        assert db.update_invoice_query(
            invoice_case, COMMUNITY, "operator", target_status=status
        )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT e.event_id,e.payload,d.delivery_id FROM operator_events e
               JOIN operator_webhook_deliveries d USING(event_id)
               ORDER BY e.event_id"""
        )
        events = cur.fetchall()
    assert len(events) == 2
    assert len({event["event_id"] for event in events}) == 2
    assert {event["payload"]["status"] for event in events} == {
        "acknowledged",
        "resolved",
    }
    case = db.list_invoice_queries(None, community_id=COMMUNITY)[0]
    assert case["status"] == "resolved"
    assert [event["new_status"] for event in case["events"]] == [
        "open",
        "acknowledged",
        "resolved",
    ]


def _case():
    return db.list_invoice_queries(None, community_id=COMMUNITY)[0]


def _counts():
    with db.get_connection() as conn, conn.cursor() as cur:
        result = {}
        for table in (
            "invoice_query_messages",
            "invoice_query_events",
            "operator_events",
            "operator_webhook_deliveries",
            "operator_action_idempotency",
        ):
            cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
            result[table] = cur.fetchone()["n"]
        return result


def _change(
    adapter,
    case_id,
    *,
    status="acknowledged",
    message="Fictional response",
    key="reply-1",
    community=COMMUNITY,
):
    if adapter == "dashboard":
        return db.update_invoice_query(
            case_id, community, "operator", message=message, target_status=status
        )
    return db.respond_operator_invoice_case(
        case_id, community, "operator", message, status, key
    )


def test_operator_replay_is_request_bound_and_survives_later_changes(invoice_case):
    from store.operator_api import event_id_for

    first = _change("operator", invoice_case)
    audit_id = _case()["events"][-1]["id"]
    assert first == {
        "id": invoice_case,
        "status": "acknowledged",
        "replayed": False,
        "event_id": event_id_for("invoice.case.updated", f"transition:{audit_id}"),
    }
    assert _change("dashboard", invoice_case, status="resolved", message="")
    before = _counts()
    replay = _change("operator", invoice_case)
    assert replay == {**first, "replayed": True}
    with pytest.raises(ValueError, match="another request"):
        _change("operator", invoice_case, message="Changed payload")
    assert _counts() == before
    assert _case()["status"] == "resolved"


@pytest.mark.parametrize("adapter", ["dashboard", "operator"])
def test_rejected_transition_has_no_partial_reply_or_event(invoice_case, adapter):
    assert _change("dashboard", invoice_case, status="resolved")
    before = _counts()
    with pytest.raises(ValueError, match="Statuswechsel"):
        _change(adapter, invoice_case)
    assert _counts() == before
    assert _case()["status"] == "resolved"


def test_dashboard_reply_only_remains_repeatable_and_status_retry_is_invalid(
    invoice_case,
):
    for _ in range(2):
        assert _change("dashboard", invoice_case, status="", message="Same reply")
    assert [row["message"] for row in _case()["messages"]] == [
        "Fictional question",
        "Same reply",
        "Same reply",
    ]
    assert _counts()["operator_events"] == 0
    assert _change("dashboard", invoice_case, message="")
    before = _counts()
    with pytest.raises(ValueError):
        _change("dashboard", invoice_case, message="")
    assert _counts() == before


@pytest.mark.parametrize("adapter", ["dashboard", "operator"])
def test_foreign_community_changes_leave_the_case_untouched(invoice_case, adapter):
    before = _counts()
    assert not _change(adapter, invoice_case, community="foreign-community")
    assert _counts() == before
    assert _case()["status"] == "open"


@pytest.mark.parametrize("adapter", ["dashboard", "operator"])
def test_failure_after_outbox_write_rolls_back_reply_status_audit_and_replay(
    invoice_case, adapter, monkeypatch
):
    from store import invoice_query

    before = _counts()
    original = invoice_query.enqueue_event

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("synthetic outbox write failure")

    with monkeypatch.context() as scoped:
        scoped.setattr(invoice_query, "enqueue_event", fail)
        with pytest.raises(RuntimeError, match="synthetic"):
            _change(adapter, invoice_case)
    assert _counts() == before
    assert _case()["status"] == "open"
    assert _change(adapter, invoice_case)


def test_failure_after_operator_replay_write_rolls_back_all_changes(
    invoice_case, monkeypatch
):
    from store import invoice_query

    before = _counts()
    original = invoice_query._remember

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("synthetic replay write failure")

    with monkeypatch.context() as scoped:
        scoped.setattr(invoice_query, "_remember", fail)
        with pytest.raises(RuntimeError, match="synthetic"):
            _change("operator", invoice_case)
    assert _counts() == before
    assert _case()["status"] == "open"
    assert _change("operator", invoice_case)["replayed"] is False


@pytest.mark.parametrize(
    "adapters,keys,replays",
    [
        (("operator", "operator"), ("same", "same"), 1),
        (("operator", "operator"), ("first", "second"), 0),
        (("dashboard", "operator"), ("first", "second"), 0),
    ],
)
def test_concurrent_changes_persist_one_transition(
    invoice_case, adapters, keys, replays
):
    barrier = Barrier(2)

    def run(adapter, key):
        barrier.wait(timeout=5)
        try:
            return _change(adapter, invoice_case, key=key)
        except ValueError:
            return "invalid transition"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(run, adapter, key) for adapter, key in zip(adapters, keys)
        ]
        results = [future.result(timeout=10) for future in futures]
    assert (
        sum(isinstance(result, dict) and result["replayed"] for result in results)
        == replays
    )
    assert results.count("invalid transition") == 1 - replays
    assert _case()["status"] == "acknowledged"
    assert _counts()["invoice_query_messages"] == 2
    assert _counts()["invoice_query_events"] == 2
    assert _counts()["operator_events"] == 1
    assert _counts()["operator_webhook_deliveries"] == 1


def test_authenticated_operator_adapter_preserves_capabilities_scope_and_response(
    invoice_case,
):
    from flask import Flask

    import operator_api

    db.create_operator_api_client(
        COMMUNITY,
        "operator",
        "Mutation client",
        ["cases.mutate"],
        hashlib.sha256(b"fictional-mutation").hexdigest(),
        None,
    )
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(operator_api.operator_api_bp)
    client = app.test_client()
    path = f"/api/operator/v1/communities/{COMMUNITY}/billing/cases/{invoice_case}/responses"
    headers = {
        "Authorization": "Bearer fictional-mutation",
        "Idempotency-Key": "http-1",
    }
    payload = {"message": "Fictional response", "status": "acknowledged"}
    before = _counts()
    denied = client.post(
        path,
        json=payload,
        headers={**headers, "Authorization": "Bearer fictional-cases"},
    )
    assert denied.status_code == 403
    foreign = client.post(
        path.replace(COMMUNITY, "foreign-community"), json=payload, headers=headers
    )
    assert foreign.status_code == 404
    missing = client.post(
        path.replace(f"/{invoice_case}/responses", "/999999/responses"),
        json={"message": "", "status": ""},
        headers=headers,
    )
    assert missing.status_code == 404
    assert _counts() == before
    response = client.post(path, json=payload, headers=headers)
    assert response.status_code == 200
    assert response.json["id"] == invoice_case
    assert response.json["status"] == "acknowledged"
    assert response.json["replayed"] is False
    replay = client.post(path, json=payload, headers=headers)
    assert replay.status_code == 200
    assert replay.json == {**response.json, "replayed": True}
    conflict = client.post(
        path, json={**payload, "message": "Different reply"}, headers=headers
    )
    assert conflict.status_code == 409
    assert _counts()["operator_events"] == 1
    assert _counts()["operator_action_idempotency"] == 1


@pytest.mark.parametrize(
    "message,status", [("", "acknowledged"), ("Reply", ""), ("Reply", None)]
)
def test_operator_requires_both_a_message_and_a_transition(
    invoice_case, message, status
):
    before = _counts()
    with pytest.raises(ValueError):
        db.respond_operator_invoice_case(
            invoice_case, COMMUNITY, "operator", message, status, "invalid"
        )
    assert _counts() == before
    assert _case()["status"] == "open"
