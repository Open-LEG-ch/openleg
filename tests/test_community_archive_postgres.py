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
        "bulk_readings",
        "bulk_readings_conflict",
        "reserved_json",
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
    if target_mode == "reserved_json":
        literal_tags = {
            "nested": [
                {"$decimal": "1.2"},
                {"$date": "2026-01-01"},
                {"$binary": "c3ludGhldGlj"},
                {"$datetime": "2026-01-01T00:00:00Z"},
                {"$date": "this is literal JSON, not a typed date"},
            ],
        }
        with db.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO billing_periods (community_id, period_start, period_end, reconciliation, billing_policy_snapshot) VALUES (%s, '2026-01-01', '2026-02-01', %s, %s)",
                (
                    cid,
                    psycopg2.extras.Json(literal_tags),
                    psycopg2.extras.Json({"$decimal": "a literal JSON object"}),
                ),
            )
    if target_mode.startswith("bulk_readings"):
        assert (
            db.upsert_metering_points(
                [
                    {
                        "metering_point_id": "SYNTHETIC-ARCHIVE-METER",
                        "building_id": "archive-founder",
                        "community_id": cid,
                        "expected_directions": ["consumption"],
                    }
                ]
            )
            == 1
        )
        with db.get_connection() as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO meter_readings
                    (building_id, timestamp, consumption_kwh, source)
                SELECT 'archive-founder', '2026-01-01'::timestamp
                       + value * interval '15 minutes', 0.1234, 'synthetic'
                FROM generate_series(0, 4999) value
            """)
            cur.execute("""
                INSERT INTO metering_point_readings
                    (metering_point_id, direction, measured_at, total_kwh,
                     grid_kwh, community_kwh, condition_code)
                SELECT 'SYNTHETIC-ARCHIVE-METER', 'consumption',
                       '2026-01-01T00:00:00+01:00'::timestamptz
                       + value * interval '15 minutes', 0.1234, 0.1234, 0, 'E01'
                FROM generate_series(0, 4999) value
            """)
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
            "bulk_readings_conflict",
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
            elif target_mode == "bulk_readings_conflict":
                with db.get_connection() as conn, conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO meter_readings
                            (id, building_id, timestamp, consumption_kwh)
                        VALUES (5000, 'unrelated', '2026-01-01', 0.4321)
                    """)
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
        if target_mode in {"conflicting_record", "bulk_readings_conflict"}:
            with pytest.raises(
                community_archive.ArchiveError, match="Conflicting record"
            ):
                community_archive.restore_community_archive(payload)
            assert db.fetch_user_communities("archive-founder") == []
            assert db.get_building("archive-founder") is None
            assert db.list_correspondence(cid) == []
            if target_mode == "conflicting_record":
                assert (
                    db.list_correspondence(other)[0]["subject"] == "Unrelated evidence"
                )
            else:
                with db.get_connection() as conn, conn.cursor() as cur:
                    cur.execute(
                        "SELECT building_id, consumption_kwh FROM meter_readings"
                    )
                    retained = cur.fetchall()
                    assert len(retained) == 1
                    assert retained[0]["building_id"] == "unrelated"
                    assert str(retained[0]["consumption_kwh"]) == "0.4321"
                    cur.execute("SELECT COUNT(*) AS count FROM metering_point_readings")
                    assert cur.fetchone()["count"] == 0
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
        elif target_mode == "bulk_readings":
            original_connection = db.get_connection
            statements = {"meter_readings": [], "metering_point_readings": []}

            @contextmanager
            def counted_connection():
                with original_connection() as connection:

                    class Connection:
                        @contextmanager
                        def cursor(self):
                            with connection.cursor() as cursor:

                                class Cursor:
                                    def __getattr__(self, name):
                                        return getattr(cursor, name)

                                    def execute(self, query, params=None):
                                        text = (
                                            query.decode()
                                            if isinstance(query, bytes)
                                            else query
                                        )
                                        if isinstance(text, str):
                                            for table, queries in statements.items():
                                                if text.startswith(
                                                    f"INSERT INTO {table} "
                                                ) or (
                                                    text.startswith("SELECT ")
                                                    and f"FROM {table} " in text
                                                ):
                                                    queries.append(text)
                                        return cursor.execute(query, params)

                                yield Cursor()

                    yield Connection()

            with monkeypatch.context() as patch:
                patch.setattr(db, "get_connection", counted_connection)
                result = community_archive.restore_community_archive(payload)
                assert result["valid"]
                assert all(
                    1 <= len(queries) <= 40 for queries in statements.values()
                ), "bulk readings must not require one database round trip per row"
                for queries in statements.values():
                    queries.clear()
                assert community_archive.restore_community_archive(payload)["valid"]
                assert all(
                    1 <= len(queries) <= 40 for queries in statements.values()
                ), "replay must also batch its conflict comparisons"
            with db.get_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) AS count FROM meter_readings WHERE building_id = 'archive-founder'"
                )
                assert cur.fetchone()["count"] == 5000
                cur.execute(
                    "SELECT COUNT(*) AS count FROM metering_point_readings WHERE metering_point_id = 'SYNTHETIC-ARCHIVE-METER'"
                )
                assert cur.fetchone()["count"] == 5000
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
