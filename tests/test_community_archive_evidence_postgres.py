# SPDX-License-Identifier: AGPL-3.0-or-later
"""Preserve accepted VNB evidence across a clean operational archive restore."""

import hashlib
import json
import os
import secrets
from contextlib import contextmanager
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras
import psycopg2.pool
import pytest

import billing_runner
import community_archive
from tests import test_billing_precision_postgres as precision
from tests import test_e66_evidence_postgres as e66

interest_database = e66.interest_database
calculated_client = e66.calculated_client
assignment_request = e66.assignment_request
configured_e66 = e66.configured_e66
rounded_delivery = precision.rounded_delivery
pytestmark = pytest.mark.integration


@contextmanager
def clean_destination(db, monkeypatch):
    parsed = urlsplit(os.environ["DATABASE_URL"])
    name = "openleg_archive_evidence_" + secrets.token_hex(6)
    admin = psycopg2.connect(urlunsplit(parsed._replace(path="/postgres")))
    admin.autocommit = True
    pool = None
    try:
        with admin.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{name}"')
        pool = psycopg2.pool.ThreadedConnectionPool(
            1,
            3,
            urlunsplit(parsed._replace(path=f"/{name}")),
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
        with monkeypatch.context() as target_patch:
            target_patch.setattr(db, "_connection_pool", pool)
            target_patch.setattr(db, "_pool_pid", os.getpid())
            target_patch.setattr(db, "_db_initialized", True)
            db.create_tables()
            yield
    finally:
        if pool:
            pool.closeall()
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.close()


def test_accepted_e66_reference_survives_clean_database_archive(
    configured_e66, monkeypatch
):
    client, db, payload, source = configured_e66
    accepted = e66.post_e66(client, source)
    assert accepted.status_code == 201
    delivery_id = accepted.get_json()["id"]
    cid = payload["community_id"]
    start, end = (
        datetime.fromisoformat(payload[key]).astimezone(ZoneInfo("Europe/Zurich"))
        for key in ("period_start", "period_end")
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM vnb_calculated_values_deliveries WHERE id = %s",
            (delivery_id,),
        )
        expected = dict(cur.fetchone())
    original_reference = db.get_validated_calculated_values(cid, start, end)
    archive = community_archive.export_community_archive(cid)
    with clean_destination(db, monkeypatch):
        dry_run = community_archive.restore_community_archive(archive, dry_run=True)
        assert dry_run == {"valid": True, "errors": [], "conflicts": []}
        assert db.fetch_user_communities("member") == []
        restored = community_archive.restore_community_archive(archive)
        assert restored["valid"], restored
        assert db.get_validated_calculated_values(cid, start, end) == original_reference
        with db.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM vnb_calculated_values_deliveries WHERE id = %s",
                (delivery_id,),
            )
            actual = dict(cur.fetchone())
        assert actual == expected
        assert bytes(actual["evidence_bytes"]) == source
        assert community_archive.restore_community_archive(archive)["restored"] is True


@pytest.mark.parametrize("state", ["draft", "corrected_issued"])
def test_billing_and_withdrawal_history_survive_clean_restore(
    rounded_delivery, monkeypatch, state
):
    client, db, payload, source = rounded_delivery
    accepted = e66.post_e66(client, source)
    assert accepted.status_code == 201
    original_id = accepted.get_json()["id"]
    cid = payload["community_id"]
    start, end = (
        datetime.fromisoformat(payload[key]).astimezone(ZoneInfo("Europe/Zurich"))
        for key in ("period_start", "period_end")
    )
    period = billing_runner.run_billing_period(cid, start, end, prepared_by="member")
    assert period["status"] == "created"
    issued = None
    if state == "corrected_issued":
        issued = db.approve_billing_period(
            period["period_id"], cid, approver_id="producer"
        )
        withdrawal = client.post(
            f"/admin/vnb-calculated-values/dietikon/{original_id}/withdrawals",
            headers={
                "X-Admin-Token": "synthetic-admin",
                "Idempotency-Key": "archive-withdraw",
            },
            json={"reason": "Synthetic corrected source follows"},
        )
        assert withdrawal.status_code == 201
        replacement = e66.post_e66(
            client, source.replace(b"SYNTHETIC-E66-1", b"SYNTHETIC-E66-2")
        )
        assert replacement.status_code == 201
        assert replacement.get_json()["id"] > original_id
    expected_reference = db.get_validated_calculated_values(cid, start, end)
    expected = {}
    with db.get_connection() as conn, conn.cursor() as cur:
        for table in (
            "vnb_calculated_values_deliveries",
            "vnb_calculated_values_events",
            "sdat_imports",
        ):
            cur.execute(f"SELECT * FROM {table} ORDER BY 1")
            expected[table] = [dict(row) for row in cur.fetchall()]
    archive = community_archive.export_community_archive(cid)
    with clean_destination(db, monkeypatch):
        restored = community_archive.restore_community_archive(archive)
        assert restored["valid"], restored
        assert community_archive.restore_community_archive(archive)["restored"] is True
        assert db.get_validated_calculated_values(cid, start, end) == expected_reference
        with db.get_connection() as conn, conn.cursor() as cur:
            for table, rows in expected.items():
                cur.execute(f"SELECT * FROM {table} ORDER BY 1")
                assert [dict(row) for row in cur.fetchall()] == rows
        invoices = db.approve_billing_period(
            period["period_id"], cid, approver_id="producer"
        )
        if issued is not None:
            assert invoices == issued
        else:
            quantities = {
                (item["participant_id"], item["item_type"]): float(item["quantity_kwh"])
                for invoice in invoices
                for item in invoice["line_items_snapshot"]
                if item["item_type"] in {"consumer_charge", "producer_credit"}
            }
            assert quantities[("member", "consumer_charge")] == 0.333
            assert quantities[("consumer-b", "consumer_charge")] == 0.667
            assert quantities[("producer", "producer_credit")] == 1
        latest_id = expected_reference["id"]
        next_event = client.post(
            f"/admin/vnb-calculated-values/dietikon/{latest_id}/withdrawals",
            headers={
                "X-Admin-Token": "synthetic-admin",
                "Idempotency-Key": "archive-next-event",
            },
            json={"reason": "Synthetic post-migration event"},
        )
        assert next_event.status_code == 201
        assert next_event.get_json()["id"] > max(
            (row["id"] for row in expected["vnb_calculated_values_events"]), default=0
        )


def _rehash(archive, table):
    encoded = json.dumps(
        archive["datasets"][table],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    archive["manifest"]["hashes"][table] = (
        "sha256:" + hashlib.sha256(encoded).hexdigest()
    )


@pytest.mark.parametrize(
    "fault",
    [
        "source_territory",
        "source_community",
        "source_bytes",
        "source_hash",
        "event_source",
        "event_territory",
    ],
)
def test_invalid_source_or_withdrawal_cannot_be_restored(
    configured_e66, monkeypatch, fault
):
    client, db, payload, source = configured_e66
    response = e66.post_e66(client, source)
    assert response.status_code == 201
    delivery_id = response.get_json()["id"]
    assert (
        client.post(
            f"/admin/vnb-calculated-values/dietikon/{delivery_id}/withdrawals",
            headers={
                "X-Admin-Token": "synthetic-admin",
                "Idempotency-Key": "archive-validator",
            },
            json={"reason": "Synthetic source withdrawn"},
        ).status_code
        == 201
    )
    archive = json.loads(
        community_archive.export_community_archive(payload["community_id"])
    )
    deliveries = "vnb_calculated_values_deliveries"
    events = "vnb_calculated_values_events"
    row = archive["datasets"][deliveries][0]
    event = archive["datasets"][events][0]
    if fault == "source_territory":
        row["territory"] = "baden"
    elif fault == "source_community":
        row["community_id"] = "foreign-leg"
    elif fault == "source_bytes":
        row["evidence_bytes"] = {"$binary": "Zm9yZWlnbg=="}
    elif fault == "source_hash":
        row["evidence_sha256"] = "f" * 64
    elif fault == "event_source":
        event["delivery_id"] = delivery_id + 100
    else:
        event["territory"] = "baden"
    _rehash(archive, deliveries)
    _rehash(archive, events)
    with clean_destination(db, monkeypatch):
        result = community_archive.restore_community_archive(json.dumps(archive))
        assert result["valid"] is False
        assert result["errors"]
        assert db.fetch_user_communities("member") == []
        with db.get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM vnb_calculated_values_deliveries")
            assert cur.fetchone()["n"] == 0


def test_legacy_archive_import_keeps_the_original_dataset_contract(
    configured_e66, monkeypatch
):
    _, db, payload, _ = configured_e66
    archive = json.loads(
        community_archive.export_community_archive(payload["community_id"])
    )
    legacy_names = {
        "buildings",
        "communities",
        "community_members",
        "consents",
        "data_consents",
        "meter_readings",
        "community_documents",
        "leg_documents",
        "correspondence_log",
        "metering_points",
        "sdat_imports",
        "metering_point_readings",
        "sdat_veracity_flags",
        "billing_tariffs",
        "billing_periods",
        "billing_line_items",
        "invoices",
        "invoice_lifecycle_events",
        "invoice_delivery_jobs",
        "invoice_corrections",
    }
    archive["manifest"]["schema_version"] = "openleg-community-archive/1"
    archive["datasets"] = {
        name: rows for name, rows in archive["datasets"].items() if name in legacy_names
    }
    archive["manifest"]["hashes"] = {
        name: value
        for name, value in archive["manifest"]["hashes"].items()
        if name in legacy_names
    }
    with clean_destination(db, monkeypatch):
        result = community_archive.restore_community_archive(json.dumps(archive))
        assert result["valid"], result
        assert (
            db.fetch_user_communities("member")[0]["community_id"]
            == payload["community_id"]
        )


@pytest.mark.parametrize("fault", ["source_hash", "event_tenant", "ledger_identity"])
def test_corrupt_owned_evidence_cannot_be_exported(configured_e66, fault):
    client, db, payload, source = configured_e66
    accepted = e66.post_e66(client, source)
    assert accepted.status_code == 201
    delivery_id = accepted.get_json()["id"]
    with db.get_connection() as conn, conn.cursor() as cur:
        if fault == "source_hash":
            cur.execute(
                "UPDATE vnb_calculated_values_deliveries SET evidence_bytes=%s WHERE id=%s",
                (b"corrupted source", delivery_id),
            )
        elif fault == "ledger_identity":
            cur.execute(
                "UPDATE sdat_imports SET vnb_community_id='OTHER-EXTERNAL-COMMUNITY'"
            )
        else:
            cur.execute(
                """INSERT INTO vnb_calculated_values_events(territory,community_id,delivery_id,action,idempotency_key,request_fingerprint,authenticated_actor,reason)
                         VALUES ('baden',%s,%s,'withdrawn','bad-event',%s,'admin-token','PRIVATE-FOREIGN-REASON')""",
                (payload["community_id"], delivery_id, "f" * 64),
            )
    with pytest.raises(
        community_archive.ArchiveError, match="VNB evidence cannot be archived safely"
    ):
        community_archive.export_community_archive(payload["community_id"])


@pytest.mark.parametrize("foreign", ["community", "tenant", "rejected"])
def test_export_omits_unowned_or_refused_raw_source_bytes(configured_e66, foreign):
    client, db, payload, source = configured_e66
    accepted = e66.post_e66(client, source)
    assert accepted.status_code == 201
    delivery_id = accepted.get_json()["id"]
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM vnb_calculated_values_deliveries WHERE id=%s", (delivery_id,)
        )
        row = dict(cur.fetchone())
        row.pop("id")
        row["content_fingerprint"] = "f" * 64
        row["source"] = "PRIVATE-FOREIGN-SOURCE"
        row["evidence_bytes"] = b"PRIVATE-FOREIGN-RAW-BYTES"
        row["evidence_sha256"] = hashlib.sha256(row["evidence_bytes"]).hexdigest()
        if foreign == "community":
            row["community_id"] = "foreign-community"
        elif foreign == "tenant":
            row["territory"] = "baden"
        else:
            row["status"] = "rejected"
        columns = list(row)
        values = [
            psycopg2.extras.Json(row[column])
            if isinstance(row[column], (dict, list))
            else row[column]
            for column in columns
        ]
        cur.execute(
            "INSERT INTO vnb_calculated_values_deliveries ("
            + ",".join(columns)
            + ") VALUES ("
            + ",".join(["%s"] * len(columns))
            + ")",
            values,
        )
    archive = community_archive.export_community_archive(payload["community_id"])
    assert b"PRIVATE-FOREIGN" not in archive
    held = json.loads(archive)["datasets"]["vnb_calculated_values_deliveries"]
    assert [row["id"] for row in held] == [delivery_id]
