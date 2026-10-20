# SPDX-License-Identifier: AGPL-3.0-or-later
"""Preserve accepted VNB evidence across a clean operational archive restore."""

import os
import secrets
from contextlib import contextmanager
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import psycopg2.extras
import psycopg2.pool
import pytest

import community_archive
from tests import test_e66_evidence_postgres as e66

interest_database = e66.interest_database
calculated_client = e66.calculated_client
assignment_request = e66.assignment_request
configured_e66 = e66.configured_e66
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
        datetime.fromisoformat(payload[key]) for key in ("period_start", "period_end")
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
