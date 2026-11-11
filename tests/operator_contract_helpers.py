# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reusable real-store operator HTTP fixture and OpenAPI response assertion.

Import operator_http and interest_database into integration test modules. Each
fixture gets an isolated PostgreSQL database; no store or domain call is mocked.
"""

import hashlib
import secrets
from dataclasses import dataclass

import pytest
from jsonschema import Draft202012Validator

from operator_api import CAPABILITIES
from tests import test_interest_postgres
from tests.test_operator_contract import make_operator_app

interest_database = test_interest_postgres.interest_database


def resolve(spec, value):
    while "$ref" in value:
        pointer = value["$ref"]
        assert pointer.startswith("#/")
        value = spec
        for part in pointer[2:].split("/"):
            value = value[part]
    return value


def validate_response(spec, path, method, response):
    operation = spec["paths"][path][method.lower()]
    assert str(response.status_code) in operation["responses"], response.get_data(
        as_text=True
    )
    contract = resolve(spec, operation["responses"][str(response.status_code)])
    assert response.mimetype == "application/json"
    schema = contract["content"]["application/json"]["schema"]
    Draft202012Validator({"components": spec["components"], **schema}).validate(
        response.get_json()
    )
    for name, header in contract.get("headers", {}).items():
        assert name in response.headers
        value = response.headers[name]
        if header["schema"].get("type") == "integer":
            value = int(value)
        Draft202012Validator(header["schema"]).validate(value)


@dataclass
class OperatorHTTP:
    db: object
    app: object
    client: object
    community_id: str
    headers: dict
    credential: dict

    def request(self, method, suffix, **kwargs):
        headers = {**self.headers, **kwargs.pop("headers", {})}
        return self.client.open(
            "/api/operator/v1/communities/" + self.community_id + suffix,
            method=method,
            headers=headers,
            **kwargs,
        )

    def credential_headers(self, capabilities, *, budget=100):
        token = "olk_" + secrets.token_urlsafe(32)
        row = self.db.create_operator_api_client(
            self.community_id,
            "contract-admin",
            "Contract fixture",
            list(capabilities),
            hashlib.sha256(token.encode()).hexdigest(),
            None,
            budget,
        )
        return {"Authorization": "Bearer " + token}, row


@pytest.fixture
def operator_http(interest_database):
    db = interest_database
    cid = "contract-leg"
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO communities(community_id,name) VALUES (%s,'Fictional LEG')",
            (cid,),
        )
    assert db.save_building(
        "contract-admin",
        "contract@example.invalid",
        {"address": "Fictional 1", "lat": 47.2, "lon": 8.2},
        {"share_with_neighbors": True},
        verified=True,
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE buildings SET city_id='contract-tenant' WHERE building_id='contract-admin'"
        )
        cur.execute(
            "UPDATE communities SET admin_building_id='contract-admin', status='signatures_pending' WHERE community_id=%s",
            (cid,),
        )
        cur.execute(
            "INSERT INTO community_members(community_id,building_id,role,status) VALUES (%s,'contract-admin','admin','confirmed')",
            (cid,),
        )
    app = make_operator_app()
    fixture = OperatorHTTP(db, app, app.test_client(), cid, {}, {})
    fixture.headers, fixture.credential = fixture.credential_headers(CAPABILITIES)
    return fixture


def seed_operator_resources(http):
    """Synthetic eligible resources for all operator operations, with real SQL."""
    cid = http.community_id
    with http.db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO leg_documents(community_id,doc_type,filename,pdf_data,signing_status) VALUES (%s,'dso_notification','signed.pdf',%s,'completed')",
            (cid, b"synthetic signed document"),
        )
        cur.execute(
            "INSERT INTO white_label_configs(territory) VALUES ('contract-tenant')"
        )
        cur.execute(
            "INSERT INTO sdat_ingestion_schedules(territory,max_attempts,retry_seconds) VALUES ('contract-tenant',1,0)"
        )
        cur.execute(
            "INSERT INTO sdat_ingestion_runs(territory,started_at,finished_at,status,attempts) VALUES ('contract-tenant',NOW(),NOW(),'failure',1) RETURNING id"
        )
        job = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO vnb_calculated_values_deliveries(contract_version,format_version,transport,territory,community_id,content_fingerprint,evidence_sha256,evidence_bytes,status) VALUES ('vnb-calculated-values/1','json/1','manual','contract-tenant',%s,%s,%s,%s,'accepted')",
            (cid, "a" * 64, "b" * 64, b"synthetic"),
        )
        cur.execute(
            "INSERT INTO billing_periods(community_id,period_start,period_end) VALUES (%s,'2026-01-01','2026-02-01')",
            (cid,),
        )
        cur.execute(
            "INSERT INTO invoices(community_id,participant_id,invoice_number,gross_chf,status,issued_at,issue_date,due_date) VALUES (%s,'contract-admin','CONTRACT-1',42.50,'issued',NOW(),'2026-01-01','2026-02-01') RETURNING id",
            (cid,),
        )
        invoice = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO invoice_lifecycle_events(invoice_id,community_id,actor_id,event_type,previous_state,new_state,idempotency_key) VALUES (%s,%s,'contract-admin','delivered','issued','delivered','fixture-delivery')",
            (invoice, cid),
        )
        cur.execute(
            "INSERT INTO invoice_queries(invoice_id,community_id,participant_id,category) VALUES (%s,%s,'contract-admin','amount') RETURNING id",
            (invoice, cid),
        )
        case = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO bank_statement_imports(community_id,actor_id,source_name,message_type,fingerprint) VALUES (%s,'contract-admin','synthetic.xml','camt.053',%s) RETURNING id",
            (cid, "c" * 64),
        )
        statement = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO bank_statement_entries(statement_import_id,community_id,entry_reference,booking_date,amount,currency,payment_reference,credit_debit_indicator,match_decision) VALUES (%s,%s,'ENTRY-1','2026-01-15',42.50,'CHF','CONTRACT-1','CRDT','unmatched') RETURNING id",
            (statement, cid),
        )
        entry = cur.fetchone()["id"]
    return {"job_id": job, "case_id": case, "entry_id": entry, "invoice_id": invoice}
