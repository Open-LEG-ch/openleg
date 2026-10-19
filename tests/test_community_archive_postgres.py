# SPDX-License-Identifier: AGPL-3.0-or-later
"""Prove archive portability through the actual PostgreSQL adapter."""

import json
import os
import secrets
from contextlib import contextmanager
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import psycopg2.extras
import psycopg2.pool
import pytest

import community_archive
from tests import test_interest_postgres

interest_database = test_interest_postgres.interest_database
pytestmark = pytest.mark.integration


class _PauseAtCommunityInsert:
    """Pause real SQL inside the public connection seam, before later inserts."""

    def __init__(self, connection, worker):
        self.connection = connection
        self.worker = worker

    @contextmanager
    def cursor(self):
        with self.connection.cursor() as cur:
            worker = self.worker

            class Cursor:
                def __getattr__(self, name):
                    return getattr(cur, name)

                def execute(self, query, params=None):
                    result = cur.execute(query, params)
                    if isinstance(query, str) and query.startswith(
                        "INSERT INTO communities "
                    ):
                        worker.staged.set()
                        if not worker.release.wait(timeout=10):
                            raise TimeoutError("restore insert was not released")
                    return result

            yield Cursor()


@pytest.mark.parametrize(
    "target_mode",
    [
        "empty",
        "advanced_sequence",
        "cached_sequence",
        "conflicting_record",
        "concurrent_writer",
        "writer_during_restore",
    ],
)
def test_real_archive_preserves_binary_json_and_next_generated_id(
    interest_database, monkeypatch, target_mode
):
    db = interest_database
    assert db.save_building(
        "archive-founder",
        "archive@example.invalid",
        {"address": "Synthetic archive 1", "lat": 47.2, "lon": 8.2},
        {"share_with_neighbors": True},
        verified=True,
    )
    cid = db.create_community_record(
        "Synthetic archive LEG", "archive-founder", "simple", ""
    )
    attachment = b"Synthetic binary evidence\x00\xff"
    entry = db.log_correspondence(
        cid,
        "in",
        "email",
        "Synthetic VNB",
        "Synthetic evidence",
        logged_by="archive-founder",
        attachment_filename="evidence.bin",
        attachment_mime="application/octet-stream",
        attachment_data=attachment,
    )
    assert entry == 1
    payload = community_archive.export_community_archive(cid)
    parsed = urlsplit(os.environ["DATABASE_URL"])
    target = f"openleg_archive_{secrets.token_hex(6)}"
    admin = psycopg2.connect(urlunsplit(parsed._replace(path="/postgres")))
    admin.autocommit = True
    pool = None
    try:
        with admin.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{target}"')
        pool = psycopg2.pool.ThreadedConnectionPool(
            1,
            3,
            urlunsplit(parsed._replace(path=f"/{target}")),
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
        monkeypatch.setattr(db, "_connection_pool", pool)
        db.create_tables()
        if target_mode == "advanced_sequence":
            with db.get_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT nextval('correspondence_log_id_seq') FROM generate_series(1, 50)"
                )
        elif target_mode == "cached_sequence":
            with db.get_connection() as conn, conn.cursor() as cur:
                cur.execute("ALTER SEQUENCE correspondence_log_id_seq CACHE 5")
        elif target_mode in {
            "conflicting_record",
            "concurrent_writer",
            "writer_during_restore",
        }:
            with db.get_connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT setval('community_members_id_seq', 100, true)")
                cur.execute("SELECT setval('consents_id_seq', 100, true)")
            assert db.save_building(
                "unrelated",
                "unrelated@example.invalid",
                {"address": "Synthetic other", "lat": 47.2, "lon": 8.2},
                {},
                verified=True,
            )
            other = db.create_community_record(
                "Unrelated LEG", "unrelated", "simple", ""
            )
            if target_mode == "conflicting_record":
                assert (
                    db.log_correspondence(
                        other, "in", "email", "Unrelated VNB", "Unrelated evidence"
                    )
                    == entry
                )
        assert db.fetch_user_communities("archive-founder") == []
        dry_run = community_archive.restore_community_archive(payload, dry_run=True)
        if target_mode == "cached_sequence":
            assert not dry_run["valid"]
            assert any("CACHE 1" in error for error in dry_run["errors"])
        else:
            assert dry_run["valid"]
        assert db.fetch_user_communities("archive-founder") == []

        if target_mode == "cached_sequence":
            assert not community_archive.restore_community_archive(payload)["valid"]
            assert db.fetch_user_communities("archive-founder") == []
            assert db.get_building("archive-founder") is None
            assert db.list_correspondence(cid) == []
            return
        if target_mode == "conflicting_record":
            with pytest.raises(
                community_archive.ArchiveError, match="Conflicting record"
            ):
                community_archive.restore_community_archive(payload)
            assert db.fetch_user_communities("archive-founder") == []
            assert db.get_building("archive-founder") is None
            assert db.list_correspondence(cid) == []
            assert db.list_correspondence(other)[0]["subject"] == "Unrelated evidence"
            return

        if target_mode in {"concurrent_writer", "writer_during_restore"}:
            decoded = community_archive._python_value(json.loads(payload)["datasets"])
            with test_interest_postgres.transaction_race(monkeypatch) as race:

                def restore_operation():
                    return community_archive.PostgresArchiveStore().restore_community(
                        decoded
                    )

                @contextmanager
                def pausing_connection():
                    with race.connection() as connection:
                        worker = getattr(race.local, "worker", None)
                        if (
                            worker
                            and worker.operation is restore_operation
                            and target_mode == "writer_during_restore"
                        ):
                            yield _PauseAtCommunityInsert(connection, worker)
                        else:
                            yield connection

                with monkeypatch.context() as patch:
                    patch.setattr(db, "get_connection", pausing_connection)
                    restore = race.start(
                        restore_operation,
                        pause_before_commit=target_mode == "concurrent_writer",
                    )
                    race.wait_until_staged(restore)
                    writer = race.start(
                        lambda: db.log_correspondence(
                            other,
                            "out",
                            "email",
                            "Unrelated VNB",
                            "Concurrent evidence",
                        )
                    )
                    try:
                        race.wait_until_blocked_by(writer, restore)
                    finally:
                        restore.release.set()
                    race.finish(restore)
                    concurrent_id = race.finish(writer)
                    assert concurrent_id is not None and concurrent_id > entry
            result = {"valid": True}
        else:
            result = community_archive.restore_community_archive(payload)

        assert result["valid"], result
        assert community_archive.export_community_archive(cid) == payload
        assert (
            bytes(db.get_correspondence_attachment(entry, cid)["attachment_data"])
            == attachment
        )
        assert db.fetch_community_with_members(cid)["members"][0]["access_roles"] == [
            "admin"
        ]
        assert community_archive.restore_community_archive(payload)["valid"]
        next_entry = db.log_correspondence(
            cid, "out", "email", "Synthetic VNB", "After restore"
        )
        assert next_entry is not None and next_entry > entry
        if target_mode == "advanced_sequence":
            assert next_entry > 50
    finally:
        if pool:
            pool.closeall()
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE "{target}" WITH (FORCE)')
        admin.close()
