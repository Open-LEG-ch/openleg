# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scheduled SDAT ingestion behaviour at its public module interface."""

import builtins
import importlib.util
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from shutil import copyfile

import pytest

import sdat_ingestion
from tests.sdat_import_helpers import (
    FIXTURE_READINGS,
    MemoryLedger,
    broken_delivery,
    delivery,
    use_storage,
)


class MemoryStore:
    def __init__(self):
        self.locked = False
        self.runs = []

    def acquire(self, territory):
        if self.locked:
            return False
        self.locked = True
        return True

    def release(self, territory):
        self.locked = False

    def record(self, territory, report):
        self.runs.append((territory, report))


def test_run_fetches_and_imports_once_and_records_counts():
    store = MemoryStore()
    fetched = []

    result = sdat_ingestion.run(
        "dietikon",
        {"local_dir": "dietikon", "max_attempts": 3},
        store=store,
        fetcher=lambda directory: (
            fetched.append(directory)
            or {"downloaded": ["a.xml"], "skipped": [], "failed": []}
        ),
        importer=lambda directory: {
            "files_imported": 1,
            "files_existing": 0,
            "readings_imported": 96,
        },
        sleeper=lambda _seconds: None,
    )

    assert result["status"] == "success"
    assert result["downloaded_files"] == 1
    assert result["imported_files"] == 1
    assert result["imported_readings"] == 96
    assert result["attempts"] == 1
    assert fetched[0].endswith("/data/sdat/dietikon")
    assert store.runs[-1][1] == result
    assert store.locked is False


def test_replay_reports_existing_documents_without_duplicate_readings():
    result = sdat_ingestion.run(
        "dietikon",
        {},
        store=MemoryStore(),
        fetcher=lambda _directory: {
            "downloaded": [],
            "skipped": ["a.xml"],
            "failed": [],
        },
        importer=lambda _directory: {
            "files_imported": 0,
            "files_existing": 1,
            "readings_imported": 0,
        },
    )

    assert result["status"] == "success"
    assert result["imported_readings"] == 0
    assert result["existing_files"] == 1


def test_concurrent_run_is_skipped_without_fetching():
    store = MemoryStore()
    store.locked = True
    called = []

    result = sdat_ingestion.run(
        "dietikon",
        {},
        store=store,
        fetcher=lambda _directory: called.append(True),
        importer=lambda _directory: {},
    )

    assert result == {"status": "locked", "territory": "dietikon"}
    assert called == []
    assert store.runs == []


def test_transient_partial_failure_retries_then_recovers_with_bounded_backoff():
    attempts = []
    sleeps = []

    def fetch(_directory):
        attempts.append(1)
        if len(attempts) == 1:
            return {"downloaded": ["a.xml"], "skipped": [], "failed": ["b.xml"]}
        return {"downloaded": ["b.xml"], "skipped": ["a.xml"], "failed": []}

    result = sdat_ingestion.run(
        "dietikon",
        {"max_attempts": 2, "retry_seconds": 4},
        store=MemoryStore(),
        fetcher=fetch,
        importer=lambda _directory: {
            "files_imported": 1,
            "files_existing": 1,
            "readings_imported": 96,
        },
        sleeper=sleeps.append,
    )

    assert result["status"] == "success"
    assert result["attempts"] == 2
    assert sleeps == [4]


def test_failure_is_actionable_without_exposing_exception_or_meter_data():
    result = sdat_ingestion.run(
        "dietikon",
        {"max_attempts": 1},
        store=MemoryStore(),
        fetcher=lambda _directory: (_ for _ in ()).throw(
            RuntimeError("password=hunter2 meter=CH123")
        ),
        importer=lambda _directory: {},
    )

    assert result["status"] == "failure"
    assert result["error"] == "fetch_failed"
    assert "hunter2" not in str(result)
    assert "CH123" not in str(result)


def test_due_schedule_uses_local_calendar_date_across_dst_boundary():
    schedule = {
        "enabled": True,
        "timezone": "Europe/Zurich",
        "local_time": "02:30",
        "last_started_at": datetime(2026, 10, 24, 0, 30, tzinfo=UTC),
    }

    # The repeated 02:30 hour on DST fallback must still produce one daily run.
    first_0230 = datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    second_0230 = datetime(2026, 10, 25, 1, 30, tzinfo=UTC)
    assert sdat_ingestion.is_due(schedule, first_0230) is True
    schedule["last_started_at"] = first_0230
    assert sdat_ingestion.is_due(schedule, second_0230) is False


def test_nonexistent_spring_time_runs_at_first_tick_after_local_time():
    schedule = {
        "enabled": True,
        "timezone": "Europe/Zurich",
        "local_time": "02:30",
        "last_started_at": datetime(2026, 3, 28, 1, 30, tzinfo=UTC),
    }

    assert (
        sdat_ingestion.is_due(schedule, datetime(2026, 3, 29, 1, 0, tzinfo=UTC)) is True
    )


def test_established_importer_adapter_returns_machine_counts(tmp_path):
    assert sdat_ingestion._default_import(str(tmp_path)) == {
        "files_imported": 0,
        "files_existing": 0,
        "readings_imported": 0,
    }


def test_importer_reuses_live_database_pool(monkeypatch, tmp_path):
    import database

    fixture = Path(__file__).with_name("fixtures") / "sdat_e66_sample.xml"
    copyfile(fixture, tmp_path / fixture.name)
    monkeypatch.setattr(
        database,
        "init_db",
        lambda: (_ for _ in ()).throw(AssertionError("must reuse live pool")),
    )
    monkeypatch.setattr(
        database,
        "get_sdat_import_index",
        lambda: {"document_ids": frozenset(), "file_names": frozenset()},
    )
    monkeypatch.setattr(
        database,
        "save_metering_point_readings",
        lambda rows, **_kwargs: {
            "new": len(rows),
            "corrected": 0,
            "unchanged": 0,
            "samples": [],
        },
    )
    monkeypatch.setattr(database, "record_sdat_import", lambda _document: True)
    monkeypatch.setattr(database, "record_sdat_veracity_flags", lambda *_args: True)

    result = sdat_ingestion._default_import(str(tmp_path))
    assert result["files_imported"] == 1
    assert result["readings_imported"] > 0


def test_due_tenants_continue_after_one_unexpected_failure():
    schedules = [
        {
            "territory": territory,
            "enabled": True,
            "timezone": "Europe/Zurich",
            "local_time": "01:00",
        }
        for territory in ("broken", "healthy")
    ]

    def runner(territory, _schedule):
        if territory == "broken":
            raise RuntimeError("database unavailable")
        return {"status": "success"}

    result = sdat_ingestion.run_due(
        schedules,
        now=datetime(2026, 9, 15, 12, tzinfo=UTC),
        runner=runner,
    )
    assert result == {"due": 2, "succeeded": 1, "failed": 1, "locked": 0}


_ENGLISH = (
    ("Dateien:", "Files:"),
    ("verarbeitet", "processed"),
    ("bereits importiert", "already imported"),
    ("Zeilen:", "Rows:"),
    ("neu", "new"),
    ("korrigiert", "corrected"),
)


@pytest.mark.parametrize("files", [0, 1])
def test_presentation_wording_cannot_change_scheduled_counts(
    monkeypatch, tmp_path, files
):
    """Rewording the command's printed summary once broke empty scheduled runs."""
    use_storage(monkeypatch, MemoryLedger())
    if files:
        delivery(tmp_path)
    real_print = builtins.print

    def english_print(*args, **kwargs):
        translated = []
        for value in args:
            text = str(value)
            for german, english in _ENGLISH:
                text = text.replace(german, english)
            translated.append(text)
        real_print(*translated, **kwargs)

    monkeypatch.setattr(builtins, "print", english_print)

    assert sdat_ingestion._default_import(str(tmp_path)) == {
        "files_imported": files,
        "files_existing": 0,
        "readings_imported": files * FIXTURE_READINGS,
    }


def test_scheduled_import_does_not_load_the_command_script(monkeypatch, tmp_path):
    use_storage(monkeypatch, MemoryLedger())
    delivery(tmp_path)

    def refuse(*_args, **_kwargs):
        raise AssertionError("scheduled import loaded the command script")

    monkeypatch.setattr(importlib.util, "spec_from_file_location", refuse)

    assert sdat_ingestion._default_import(str(tmp_path))["files_imported"] == 1


def test_scheduled_import_leaves_other_threads_output_alone(
    monkeypatch, tmp_path, capsys
):
    """A running import must not capture what another thread prints."""
    storage = use_storage(monkeypatch, MemoryLedger())
    delivery(tmp_path)
    saving = threading.Event()
    finish = threading.Event()
    real_save = storage.save_metering_point_readings

    def held_save(rows, **kwargs):
        saving.set()
        assert finish.wait(10), "test did not release the import"
        return real_save(rows, **kwargs)

    monkeypatch.setattr(
        sys.modules["database"], "save_metering_point_readings", held_save
    )
    results = []
    worker = threading.Thread(
        target=lambda: results.append(sdat_ingestion._default_import(str(tmp_path)))
    )
    worker.start()
    try:
        assert saving.wait(10), "import never reached storage"
        stdout_while_importing = sys.stdout
        print("another request's output")
    finally:
        finish.set()
        worker.join(10)

    assert results, "scheduled import did not finish"
    assert results[0]["files_imported"] == 1
    assert stdout_while_importing is sys.stdout
    assert "another request's output" in capsys.readouterr().out


def test_retries_keep_counts_from_every_attempt_of_the_shared_import(
    monkeypatch, tmp_path
):
    """A partial import counts its good file even when a later attempt succeeds."""
    monkeypatch.setenv("SWISSELDEX_SDAT_DIR", str(tmp_path))
    storage = use_storage(monkeypatch, MemoryLedger())
    attempts = []

    def fetch(directory):
        Path(directory).mkdir(exist_ok=True)
        attempts.append(directory)
        if len(attempts) == 1:
            delivery(directory, "20260807_120000_good.xml")
            broken_delivery(directory, "20260807_130000_late.xml")
        else:
            delivery(
                directory,
                "20260807_130000_late.xml",
                document_id="TESTDOC-2",
                corrected=True,
            )
        return {"downloaded": ["delivery"], "skipped": [], "failed": []}

    result = sdat_ingestion.run(
        "dietikon",
        {"max_attempts": 2, "retry_seconds": 0},
        store=MemoryStore(),
        fetcher=fetch,
        sleeper=lambda _seconds: None,
    )

    assert result["status"] == "success"
    assert result["attempts"] == 2
    assert result["imported_files"] == 2
    assert result["existing_files"] == 1
    assert result["imported_readings"] == FIXTURE_READINGS + 1
    assert storage.saved == ["TESTDOC-1", "TESTDOC-2"]
    assert "20260807" not in str(result), "file names stay out of scheduler reports"
