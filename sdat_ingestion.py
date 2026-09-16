# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scheduled SDAT ingestion orchestration.

Transport remains in :mod:`sdat_datahub`; parsing and persistence remain in the
existing import command. This module owns scheduling, retry, locking and the
small operational report returned to callers.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import sdat_datahub

logger = logging.getLogger(__name__)
DEFAULT_LOCAL_DIR = "data/sdat"


class IngestionError(RuntimeError):
    """A pipeline stage failed with a safe operator-facing error code."""

    def __init__(self, code: str, counts: dict | None = None):
        super().__init__(code)
        self.code = code
        self.counts = counts or {}


class DatabaseStore:
    """Database adapter for locks and run reports."""

    def acquire(self, territory):
        import database as db

        return db.acquire_sdat_ingestion_lock(territory)

    def release(self, territory):
        import database as db

        db.release_sdat_ingestion_lock(territory)

    def record(self, territory, report):
        import database as db

        db.record_sdat_ingestion_run(territory, report)


def _default_fetch(directory: str) -> dict:
    config = sdat_datahub.load_config()
    config.local_dir = directory
    return sdat_datahub.fetch_latest(config)


def resolve_tenant_directory(territory: str, configured: str | None = None) -> str:
    """Confine a tenant's delivery directory below the configured SDAT root."""
    root = Path(os.getenv("SWISSELDEX_SDAT_DIR", DEFAULT_LOCAL_DIR)).resolve()
    relative = Path(configured or territory)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in ("..", "") for part in relative.parts)
    ):
        raise IngestionError("invalid_local_dir")
    if not all(re.fullmatch(r"[A-Za-z0-9._-]+", part) for part in relative.parts):
        raise IngestionError("invalid_local_dir")
    candidate = (root / relative).resolve()
    if candidate == root:
        raise IngestionError("invalid_local_dir")
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise IngestionError("invalid_local_dir") from error
    return str(candidate)


def _default_import(directory: str) -> dict:
    """Run the established importer while keeping private output out of logs."""
    import database

    script = Path(__file__).with_name("scripts") / "import_sdat.py"
    spec = importlib.util.spec_from_file_location("scheduled_import_sdat", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    class RunningDatabase:
        """Use the app's live pool; the CLI normally initializes its own."""

        @staticmethod
        def init_db():
            return True

        def __getattr__(self, name):
            return getattr(database, name)

    module.db = RunningDatabase()
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        exit_code = module.main([directory, "--quiet"])
    text = output.getvalue()
    files = re.search(r"Dateien: (\d+) verarbeitet, (\d+) bereits importiert", text)
    readings = re.search(r"Zeilen: neu (\d+), korrigiert (\d+)", text)
    if not files or not readings:
        raise IngestionError("import_report_unavailable")
    counts = {
        "files_imported": int(files.group(1)),
        "files_existing": int(files.group(2)),
        "readings_imported": int(readings.group(1)) + int(readings.group(2)),
    }
    if exit_code:
        raise IngestionError("import_failed", counts)
    return counts


def run(
    territory: str,
    schedule: dict,
    *,
    store=None,
    fetcher=None,
    importer=None,
    sleeper=time.sleep,
) -> dict:
    """Fetch and import one tenant's delivery, with bounded retry."""
    store = store or DatabaseStore()
    max_attempts = max(1, min(int(schedule.get("max_attempts", 3)), 5))
    retry_seconds = max(0, min(int(schedule.get("retry_seconds", 30)), 300))
    directory = resolve_tenant_directory(territory, schedule.get("local_dir"))
    fetcher = fetcher or _default_fetch
    importer = importer or _default_import
    if not store.acquire(territory):
        return {"status": "locked", "territory": territory}

    started_at = datetime.now(timezone.utc)
    report = None
    downloaded_names = set()
    imported_totals = {
        "files_imported": 0,
        "files_existing": 0,
        "readings_imported": 0,
    }
    try:
        for attempt in range(1, max_attempts + 1):
            stage = "fetch"
            try:
                fetched = fetcher(directory)
                downloaded_names.update(fetched.get("downloaded", ()))
                if fetched.get("failed"):
                    raise IngestionError("fetch_partial_failure")
                stage = "import"
                imported = importer(directory)
                for key in imported_totals:
                    imported_totals[key] += imported.get(key, 0)
                report = {
                    "status": "success",
                    "territory": territory,
                    "started_at": started_at,
                    "finished_at": datetime.now(timezone.utc),
                    "attempts": attempt,
                    "downloaded_files": len(downloaded_names),
                    "existing_downloads": len(fetched.get("skipped", ())),
                    "imported_files": imported_totals["files_imported"],
                    "existing_files": imported_totals["files_existing"],
                    "imported_readings": imported_totals["readings_imported"],
                    "error": None,
                }
                break
            except Exception as exc:
                code = (
                    exc.code if isinstance(exc, IngestionError) else f"{stage}_failed"
                )
                if isinstance(exc, IngestionError):
                    for key in imported_totals:
                        imported_totals[key] += exc.counts.get(key, 0)
                if attempt == max_attempts:
                    report = {
                        "status": "failure",
                        "territory": territory,
                        "started_at": started_at,
                        "finished_at": datetime.now(timezone.utc),
                        "attempts": attempt,
                        "downloaded_files": len(downloaded_names),
                        "existing_downloads": 0,
                        "imported_files": imported_totals["files_imported"],
                        "existing_files": imported_totals["files_existing"],
                        "imported_readings": imported_totals["readings_imported"],
                        "error": code,
                    }
                    logger.warning(
                        "SDAT ingestion failed for tenant %s: %s", territory, code
                    )
                else:
                    sleeper(min(retry_seconds * (2 ** (attempt - 1)), 300))
        store.record(territory, report)
        return report
    finally:
        store.release(territory)


def is_due(schedule: dict, now: datetime | None = None) -> bool:
    """Return whether the tenant's once-daily local schedule is due."""
    if not schedule.get("enabled"):
        return False
    now = now or datetime.now(timezone.utc)
    try:
        local_now = now.astimezone(ZoneInfo(schedule.get("timezone", "Europe/Zurich")))
    except ZoneInfoNotFoundError:
        return False
    try:
        raw_time = schedule["local_time"]
        if hasattr(raw_time, "hour"):
            hour, minute = raw_time.hour, raw_time.minute
        else:
            hour, minute = (int(part) for part in raw_time.split(":"))
    except (KeyError, TypeError, ValueError):
        return False
    if (local_now.hour, local_now.minute) < (hour, minute):
        # On the spring DST jump, 02:xx does not exist. The first 03:xx tick
        # still compares later and runs the job.
        return False
    last = schedule.get("last_started_at")
    return not last or last.astimezone(local_now.tzinfo).date() < local_now.date()


def run_due(schedules: list[dict], *, now=None, runner=run) -> dict:
    """Run every due tenant schedule and return aggregate cron counts."""
    due = [schedule for schedule in schedules if is_due(schedule, now)]
    results = []
    for schedule in due:
        try:
            results.append(runner(schedule["territory"], schedule))
        except Exception:
            # Keep one unavailable tenant from starving every schedule. Do not
            # log exception text because connector failures can contain secrets.
            logger.error(
                "Unexpected SDAT ingestion failure for tenant %s",
                schedule["territory"],
            )
            results.append({"status": "failure"})
    return {
        "due": len(due),
        "succeeded": sum(item["status"] == "success" for item in results),
        "failed": sum(item["status"] == "failure" for item in results),
        "locked": sum(item["status"] == "locked" for item in results),
    }
