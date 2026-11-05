# SPDX-License-Identifier: AGPL-3.0-or-later
"""Invoice questions retain ordinary case identities and private scope."""

import io

import pytest

from tests import test_sandbox_http
from tests.test_sandbox_metering import _workspace_connection
from tests.test_sandbox_payments import _issued
from tests.test_sandbox_personas import _start, _switch

village = test_sandbox_http.village
_csrf = test_sandbox_http._csrf
pytestmark = pytest.mark.integration


def test_question_history_uses_ordinary_transitions_and_keyed_replay(village):
    client = village.test_client()
    prefix, invoices = _issued(village, client)
    path = prefix + "/sandbox/questions"
    _switch(client, prefix, "resident")
    assert client.get(path).status_code == 200
    invoice = invoices["sonnwil-resident"]
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cur,
    ):
        cur.execute("SELECT row_to_json(i) FROM invoices i ORDER BY id")
        before = cur.fetchall()
    page = client.get(prefix + f"/dashboard/invoices/{invoice}")
    opened = client.post(
        prefix + f"/dashboard/invoices/{invoice}/queries",
        data={
            "csrf_token": _csrf(page),
            "category": "amount",
            "message": "SIMULATION why 43.20?",
            "attachment": (
                io.BytesIO(b"%PDF-1.4\nSIMULATION own receipt\n%%EOF"),
                "simulation.pdf",
                "application/pdf",
            ),
        },
        content_type="multipart/form-data",
    )
    assert opened.status_code == 302
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cur,
    ):
        cur.execute("SELECT id FROM invoice_queries")
        case = cur.fetchone()[0]
        cur.execute("SELECT id FROM invoice_query_messages WHERE query_id=%s", (case,))
        message = cur.fetchone()[0]
    attachment = path + f"/attachments/{message}"
    assert client.get(attachment).data.startswith(b"%PDF-")
    _switch(client, prefix, "operator")
    post = path + f"/{case}/respond"
    page = client.get(path)
    fields = {
        "csrf_token": _csrf(page),
        "key": "acknowledge-one",
        "confirm": "yes",
        "message": "SIMULATION received",
        "status": "acknowledged",
    }
    for _ in range(2):
        assert client.post(post, data=fields).status_code == 303
    assert (
        client.post(post, data={**fields, "message": "Changed retry"}).status_code
        == 409
    )
    assert (
        client.post(
            post,
            data={
                **fields,
                "key": "resolve-two",
                "message": "SIMULATION 360 kWh at 12 Rp",
                "status": "resolved",
            },
        ).status_code
        == 303
    )
    assert client.post(post, data=fields).status_code == 303
    assert client.post(post, data={**fields, "key": "invalid-three"}).status_code == 409
    assert client.get(attachment).data.startswith(b"%PDF-")
    _switch(client, prefix, "resident")
    for url in (path, prefix + f"/dashboard/invoices/{invoice}"):
        text = client.get(url).text
        assert "SIMULATION received" in text and "SIMULATION 360 kWh at 12 Rp" in text
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cur,
    ):
        cur.execute("SELECT row_to_json(i) FROM invoices i ORDER BY id")
        assert cur.fetchall() == before
        cur.execute(
            "SELECT new_status FROM invoice_query_events WHERE query_id=%s ORDER BY id",
            (case,),
        )
        assert cur.fetchall() == [("open",), ("acknowledged",), ("resolved",)]
        cur.execute(
            "SELECT COUNT(*) FROM invoice_query_messages WHERE query_id=%s", (case,)
        )
        assert cur.fetchone()[0] == 3
        cur.execute(
            "SELECT event_id FROM operator_events WHERE event_type='invoice.case.updated'"
        )
        events = cur.fetchall()
        assert len(events) == len(set(events)) == 2
        assert all(str(event[0]) in client.get(path).text for event in events)


def test_question_attachment_and_reply_scope_follow_current_authority(
    village, monkeypatch
):
    client = village.test_client()
    prefix, invoices = _issued(village, client)
    path = prefix + "/sandbox/questions"
    _switch(client, prefix, "resident")
    invoice = invoices["sonnwil-resident"]
    page = client.get(prefix + f"/dashboard/invoices/{invoice}")
    fields = {
        "csrf_token": _csrf(page),
        "category": "amount",
        "message": "SIMULATION PRIVATE-QUESTION",
    }
    opened = client.post(
        prefix + f"/dashboard/invoices/{invoice}/queries",
        data={
            **fields,
            "attachment": (
                io.BytesIO(b"%PDF-1.4\nPRIVATE-ATTACHMENT\n%%EOF"),
                "private.pdf",
                "application/pdf",
            ),
        },
        content_type="multipart/form-data",
    )
    assert opened.status_code == 302
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cur,
    ):
        cur.execute("SELECT id FROM invoice_queries")
        case = cur.fetchone()[0]
        cur.execute("SELECT id FROM invoice_query_messages")
        message = cur.fetchone()[0]
    attachment = path + f"/attachments/{message}"
    response = path + f"/{case}/respond"
    _switch(client, prefix, "producer")
    page = client.get(path)
    assert page.status_code == 200 and "PRIVATE-QUESTION" not in page.text
    assert client.get(attachment).status_code == 404
    assert client.get(prefix + f"/dashboard/invoices/{invoice}").status_code == 404
    assert (
        client.post(
            prefix + f"/dashboard/invoices/{invoice}/queries/{case}/reply",
            data={
                "csrf_token": _csrf(client.get(prefix + "/dashboard")),
                "message": "Unauthorized",
            },
        ).status_code
        == 400
    )
    assert (
        client.post(
            response,
            data={
                "csrf_token": _csrf(client.get(prefix + "/dashboard")),
                "key": "unauthorized",
                "confirm": "yes",
                "status": "resolved",
            },
        ).status_code
        == 403
    )
    _switch(client, prefix, "operator")
    page = client.get(path)
    fields = {
        "csrf_token": _csrf(page),
        "key": "valid-key",
        "confirm": "yes",
        "status": "acknowledged",
    }
    assert client.post(response, data={**fields, "csrf_token": ""}).status_code == 400
    assert client.post(response, data={**fields, "confirm": ""}).status_code == 400
    assert client.get(attachment).status_code == 200
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cur,
    ):
        cur.execute(
            "UPDATE community_members SET access_roles=%s::jsonb WHERE community_id='sonnwil-operating-leg' AND building_id='sonnwil-operator'",
            ('["metering"]',),
        )
    page = client.get(path)
    assert "PRIVATE-QUESTION" not in page.text and "private.pdf" not in page.text
    assert client.get(attachment).status_code == 404
    assert client.post(response, data=fields).status_code == 403
    other = village.test_client()
    other_prefix, _ = _start(other, "operator")
    assert other.get(path).status_code == 410
    assert (
        other.get(
            other_prefix + f"/sandbox/questions/attachments/{message}"
        ).status_code
        == 404
    )
    assert (
        other.post(
            other_prefix + f"/sandbox/questions/{case}/respond",
            data={
                **fields,
                "csrf_token": _csrf(other.get(other_prefix + "/dashboard")),
            },
        ).status_code
        == 404
    )
    from flask import Flask

    import sandbox_questions

    monkeypatch.delenv("OPENLEG_SANDBOX_WORKER", raising=False)
    with pytest.raises(RuntimeError, match="requires a village worker"):
        sandbox_questions.install(Flask(__name__))


def test_foreign_community_case_messages_evidence_and_events_are_not_exposed(village):
    from uuid import uuid4

    client = village.test_client()
    prefix, _ = _start(client, "operator")
    path = prefix + "/sandbox/questions"
    foreign_event = str(uuid4())
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cur,
    ):
        cur.execute(
            "INSERT INTO communities(community_id,name) VALUES ('foreign-leg','SIMULATION FOREIGN')"
        )
        cur.execute(
            "INSERT INTO invoices(community_id,participant_id,invoice_number,status,gross_chf) VALUES ('foreign-leg','foreign-participant','SIMULATION-FOREIGN', 'issued',43.20) RETURNING id"
        )
        invoice = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO invoice_queries(invoice_id,community_id,participant_id,category,status,response_due_at) VALUES (%s,'foreign-leg','foreign-participant','amount','open',CURRENT_TIMESTAMP) RETURNING id",
            (invoice,),
        )
        case = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO invoice_query_messages(query_id,actor_id,message,attachment_filename,attachment_data) VALUES (%s,'foreign-participant','FOREIGN-SECRET-MESSAGE','FOREIGN-SECRET.pdf',%s) RETURNING id",
            (case, b"%PDF-1.4\nFOREIGN-SECRET"),
        )
        message = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO operator_events(event_id,event_type,schema_version,aggregate_id,community_id,payload) VALUES (%s,'invoice.case.updated','operator-event/1',%s,'foreign-leg','{}')",
            (foreign_event, str(case)),
        )
    page = client.get(path)
    assert (
        page.status_code == 200
        and "FOREIGN-SECRET" not in page.text
        and foreign_event not in page.text
    )
    assert client.get(path + f"/attachments/{message}").status_code == 404
    csrf = _csrf(client.get(prefix + "/dashboard"))
    assert (
        client.post(
            path + f"/{case}/respond",
            data={
                "csrf_token": csrf,
                "confirm": "yes",
                "key": "foreign-denied",
                "status": "resolved",
            },
        ).status_code
        == 404
    )
    _switch(client, prefix, "resident")
    assert client.get(path + f"/attachments/{message}").status_code == 404
    page = client.get(path)
    assert "FOREIGN-SECRET" not in page.text and foreign_event not in page.text
