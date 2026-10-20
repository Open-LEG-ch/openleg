# SPDX-License-Identifier: AGPL-3.0-or-later
"""Retain VNB handover evidence when moving one LEG to a clean database."""

import json

import psycopg2.extras
import pytest

import community_archive
from tests import test_interest_postgres
from tests.test_community_archive_evidence_postgres import clean_destination

interest_database = test_interest_postgres.interest_database

pytestmark = pytest.mark.integration


def _handover_records(db, cid, suffix, event_id):
    case = "formation-" + suffix
    mutation = "mutation-" + suffix
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO vnb_submission_cases
            (case_id, community_id, created_by, adapter_key, contract_version,
             payload_fingerprint, state, next_action, manual_package, response_evidence)
            VALUES (%s,%s,'founder','manual-handover','vnb-formation/1',%s,
                    'prepared','download_and_deliver',%s,%s)""",
            (
                case,
                cid,
                "a" * 64,
                ("formation package " + suffix).encode(),
                ("acknowledgement " + suffix).encode(),
            ),
        )
        cur.execute(
            """INSERT INTO vnb_mutation_cases
            (case_id,mutation_id,community_id,participant_id,created_by,
             mutation_type,effective_date,source_agreement_id,before_facts,after_facts,
             adapter_key,contract_version,payload_fingerprint,state,next_action,manual_package)
            VALUES (%s,%s,%s,'founder','founder','change','2026-10-01','agreement-v3',
                    %s,%s,'manual-handover','vnb-membership/1',%s,'acknowledged','none',%s)""",
            (
                mutation,
                "change-" + suffix,
                cid,
                psycopg2.extras.Json({"role": "member"}),
                psycopg2.extras.Json({"role": "admin"}),
                "b" * 64,
                ("mutation package " + suffix).encode(),
            ),
        )
        cur.execute(
            """INSERT INTO vnb_mutation_events
            (id,case_id,state,external_request_id,response_status,evidence)
            VALUES (%s,%s,'acknowledged',%s,'accepted',%s)""",
            (
                event_id,
                mutation,
                "external-" + suffix,
                ("mutation response " + suffix).encode(),
            ),
        )
    return case, mutation


def test_vnb_handover_history_round_trips_without_other_community_records(
    interest_database, monkeypatch
):
    db = interest_database
    assert db.save_building(
        "founder",
        "founder@example.invalid",
        {"address": "Synthetic 1", "lat": 47.2, "lon": 8.2},
        {},
        verified=True,
    )
    cid = db.create_community_record("Archive LEG", "founder", "simple", "")
    assert db.save_building(
        "outsider",
        "outsider@example.invalid",
        {"address": "Synthetic 2", "lat": 47.2, "lon": 8.2},
        {},
        verified=True,
    )
    foreign = db.create_community_record("Other LEG", "outsider", "simple", "")
    case, mutation = _handover_records(db, cid, "own", 80)
    _handover_records(db, foreign, "PRIVATE-FOREIGN", 81)
    tables = {
        "vnb_submission_cases": ("case_id", case),
        "vnb_mutation_cases": ("case_id", mutation),
        "vnb_mutation_events": ("case_id", mutation),
    }
    expected = {}
    with db.get_connection() as conn, conn.cursor() as cur:
        for table, (column, value) in tables.items():
            cur.execute(f"SELECT * FROM {table} WHERE {column} = %s", (value,))
            expected[table] = [dict(row) for row in cur.fetchall()]
    archive = community_archive.export_community_archive(cid)
    assert b"PRIVATE-FOREIGN" not in archive
    payload = json.loads(archive)
    assert set(tables) <= payload["datasets"].keys()
    with clean_destination(db, monkeypatch):
        dry_run = community_archive.restore_community_archive(archive, dry_run=True)
        assert dry_run["valid"], dry_run
        assert db.fetch_user_communities("founder") == []
        result = community_archive.restore_community_archive(archive)
        assert result["valid"], result
        assert community_archive.restore_community_archive(archive)["restored"] is True
        with db.get_connection() as conn, conn.cursor() as cur:
            for table, rows in expected.items():
                cur.execute(f"SELECT * FROM {table}")
                assert [dict(row) for row in cur.fetchall()] == rows
            cur.execute(
                "INSERT INTO vnb_mutation_events(case_id,state,external_request_id) VALUES (%s,'delivered','post-restore') RETURNING id",
                (mutation,),
            )
            assert cur.fetchone()["id"] > 80
        assert db.list_vnb_submission_cases(cid)[0]["case_id"] == case
        assert db.fetch_user_communities("outsider") == []
