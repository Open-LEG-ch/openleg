# SPDX-License-Identifier: AGPL-3.0-or-later
"""Persistence for per-tenant SDAT schedules, locks and run history."""

import json
import threading

from store.operator_api import enqueue_event

_lock_guard = threading.Lock()
_lock_connections = {}


def _get_connection():
    import database

    return database.get_connection()


def acquire_sdat_ingestion_lock(territory: str) -> bool:
    """Hold a PostgreSQL advisory lock on a dedicated session until release."""
    with _lock_guard:
        if territory in _lock_connections:
            return False
        _lock_connections[territory] = None
    context = None
    entered = False
    try:
        context = _get_connection()
        conn = context.__enter__()
        entered = True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_try_advisory_lock(hashtext(%s)) AS acquired",
                ("sdat:" + territory,),
            )
            row = cur.fetchone()
            acquired = bool(row.get("acquired") if hasattr(row, "get") else row[0])
    except BaseException:
        if context is not None and entered:
            context.__exit__(*__import__("sys").exc_info())
        with _lock_guard:
            _lock_connections.pop(territory, None)
        raise
    if not acquired:
        context.__exit__(None, None, None)
        with _lock_guard:
            _lock_connections.pop(territory, None)
        return False
    with _lock_guard:
        _lock_connections[territory] = (context, conn)
    return True


def release_sdat_ingestion_lock(territory: str) -> None:
    with _lock_guard:
        held = _lock_connections.pop(territory, None)
    if not held:
        return
    context, conn = held
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_advisory_unlock(hashtext(%s))", ("sdat:" + territory,)
            )
    finally:
        context.__exit__(None, None, None)


def list_sdat_ingestion_schedules() -> list[dict]:
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.*, r.started_at AS last_started_at,
                   r.finished_at AS last_finished_at, r.status AS last_status,
                   r.downloaded_files, r.imported_files, r.imported_readings,
                   r.error_code, ok.finished_at AS last_success_at,
                   failed.finished_at AS last_failure_at,
                   failed.error_code AS last_failure_error
            FROM sdat_ingestion_schedules s
            LEFT JOIN LATERAL (
                SELECT * FROM sdat_ingestion_runs
                WHERE territory = s.territory ORDER BY started_at DESC LIMIT 1
            ) r ON TRUE
            LEFT JOIN LATERAL (
                SELECT finished_at FROM sdat_ingestion_runs
                WHERE territory = s.territory AND status = 'success'
                ORDER BY started_at DESC LIMIT 1
            ) ok ON TRUE
            LEFT JOIN LATERAL (
                SELECT finished_at, error_code FROM sdat_ingestion_runs
                WHERE territory = s.territory AND status = 'failure'
                ORDER BY started_at DESC LIMIT 1
            ) failed ON TRUE
            ORDER BY s.territory
            """
        )
        schedules = [dict(row) for row in cur.fetchall()]
        for schedule in schedules:
            local_time = schedule.get("local_time")
            if hasattr(local_time, "strftime"):
                schedule["local_time"] = local_time.strftime("%H:%M")
        return schedules


def upsert_sdat_ingestion_schedule(territory: str, schedule: dict) -> dict:
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sdat_ingestion_schedules
                (territory, enabled, timezone, local_time, local_dir,
                 max_attempts, retry_seconds)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (territory) DO UPDATE SET
                enabled = EXCLUDED.enabled, timezone = EXCLUDED.timezone,
                local_time = EXCLUDED.local_time, local_dir = EXCLUDED.local_dir,
                max_attempts = EXCLUDED.max_attempts,
                retry_seconds = EXCLUDED.retry_seconds,
                updated_at = CURRENT_TIMESTAMP
            RETURNING *
            """,
            (
                territory,
                schedule["enabled"],
                schedule["timezone"],
                schedule["local_time"],
                schedule.get("local_dir"),
                schedule["max_attempts"],
                schedule["retry_seconds"],
            ),
        )
        return dict(cur.fetchone())


def record_sdat_ingestion_run(territory: str, report: dict) -> None:
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sdat_ingestion_runs
                (territory, started_at, finished_at, status, attempts,
                 downloaded_files, imported_files, imported_readings, error_code,
                 report)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                territory,
                report["started_at"],
                report["finished_at"],
                report["status"],
                report["attempts"],
                report["downloaded_files"],
                report["imported_files"],
                report["imported_readings"],
                report.get("error"),
                json.dumps(report, default=str),
            ),
        )
        run_id = cur.fetchone()["id"]
        cur.execute(
            """SELECT c.community_id FROM communities c
                 JOIN buildings b ON b.building_id=c.admin_building_id
                WHERE b.city_id=%s""",
            (territory,),
        )
        for community in cur.fetchall():
            enqueue_event(
                cur,
                "metering.ingestion.completed",
                f"{community['community_id']}:{run_id}",
                community["community_id"],
                {"status": report["status"], "error_code": report.get("error")},
            )
