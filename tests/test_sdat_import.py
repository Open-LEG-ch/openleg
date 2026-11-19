# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shared SDAT import and its two adapters.

The import command and scheduled ingestion read the same ``ImportReport``.
Each scenario runs the import module, the command and the scheduler adapter
against one in-memory ledger behind the ``database`` facade.
"""

import gzip
import importlib.util
from pathlib import Path

import pytest

import sdat_import
import sdat_ingestion
from tests.sdat_import_helpers import (
    DOCUMENT_ID,
    FIXTURE,
    FIXTURE_READINGS,
    MemoryLedger,
    broken_delivery,
    delivery,
    use_storage,
)

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "import_sdat.py"
POINT_ONE = "CH000000000000000000000000000001"


@pytest.fixture
def command():
    spec = importlib.util.spec_from_file_location("import_sdat_adapter", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def ledger(monkeypatch):
    return use_storage(monkeypatch, MemoryLedger())


class RefusingStorage:
    """Storage that fails the test on any use."""

    def __getattr__(self, name):
        def refuse(*_args, **_kwargs):
            raise AssertionError(f"storage used: {name}")

        return refuse


def _summary(output):
    return [
        line for line in output.splitlines() if line.startswith(("Dateien", "Zeilen"))
    ]


# ==== Empty runs ====


def test_an_empty_run_touches_no_storage_in_either_adapter(
    monkeypatch, tmp_path, command, capsys
):
    use_storage(monkeypatch, RefusingStorage())

    report = sdat_import.import_paths(
        [str(tmp_path)], initialize=RefusingStorage().init_db
    )
    assert report == sdat_import.ImportReport()
    assert report.succeeded

    assert command.main([str(tmp_path)]) == 0
    assert _summary(capsys.readouterr().out) == [
        "Dateien: 0 verarbeitet, 0 bereits importiert, 0 übersprungen, 0 fehlerhaft",
        "Zeilen: neu 0, korrigiert 0, unverändert 0",
    ]

    assert sdat_ingestion._default_import(str(tmp_path)) == {
        "files_imported": 0,
        "files_existing": 0,
        "readings_imported": 0,
    }


# ==== Replay ====


def test_a_replayed_delivery_is_existing_in_every_adapter(
    ledger, tmp_path, command, capsys
):
    delivery(tmp_path)
    first = sdat_import.import_paths([str(tmp_path)])
    assert first.count(sdat_import.IMPORTED) == 1
    assert first.readings.new == FIXTURE_READINGS

    replay = sdat_import.import_paths([str(tmp_path)])
    (outcome,) = replay.files
    assert outcome.status == sdat_import.ALREADY
    assert outcome.name == "sample.xml", "the packed file keeps its plain name"
    assert replay.readings == sdat_import.ReadingCounts()

    assert command.main([str(tmp_path)]) == 0
    assert _summary(capsys.readouterr().out) == [
        "Dateien: 0 verarbeitet, 1 bereits importiert, 0 übersprungen, 0 fehlerhaft",
        "Zeilen: neu 0, korrigiert 0, unverändert 0",
    ]

    assert sdat_ingestion._default_import(str(tmp_path)) == {
        "files_imported": 0,
        "files_existing": 1,
        "readings_imported": 0,
    }
    assert ledger.saved == [DOCUMENT_ID], "a replay never writes readings again"


# ==== Corrected readings ====


@pytest.mark.parametrize("adapter", ["module", "command", "scheduler"])
def test_a_corrected_reading_reaches_every_adapter(
    ledger, tmp_path, command, capsys, adapter
):
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    delivery(baseline)
    sdat_import.import_paths([str(baseline)])
    correction = tmp_path / "correction"
    correction.mkdir()
    delivery(correction, document_id="TESTDOC-2", corrected=True)

    if adapter == "module":
        report = sdat_import.import_paths([str(correction)])
        (outcome,) = report.files
        assert outcome.readings.new == 0
        assert outcome.readings.corrected == 1
        assert outcome.readings.unchanged == FIXTURE_READINGS - 1
        assert [sample[0] for sample in outcome.readings.samples] == [POINT_ONE]
    elif adapter == "command":
        assert command.main([str(correction)]) == 0
        output = capsys.readouterr().out
        assert "Zeilen: neu 0, korrigiert 1, unverändert 8" in output
        assert "Korrektur:" in output
        assert POINT_ONE not in output, "the command masks metering point ids"
    else:
        assert sdat_ingestion._default_import(str(correction)) == {
            "files_imported": 1,
            "files_existing": 0,
            "readings_imported": 1,
        }


# ==== Partial failure ====


def test_a_partial_failure_keeps_the_good_file_in_the_module(ledger, tmp_path):
    delivery(tmp_path, "20260807_120000_good.xml")
    broken_delivery(tmp_path, "20260807_130000_broken.xml")

    report = sdat_import.import_paths([str(tmp_path)])

    assert [(item.name, item.status) for item in report.files] == [
        ("20260807_120000_good.xml", sdat_import.IMPORTED),
        ("20260807_130000_broken.xml", sdat_import.FAILED),
    ]
    assert {item.code for item in report.files[1].diagnostics} == {
        sdat_import.INVALID_DOCUMENT
    }
    assert not report.succeeded
    assert ledger.saved == [DOCUMENT_ID]
    assert (tmp_path / "20260807_120000_good.xml.gz").exists()
    assert (tmp_path / "20260807_130000_broken.xml").exists()


@pytest.mark.parametrize(
    "intact", [0, 2 * sdat_import.HEAD_LIMIT], ids=["head", "body"]
)
def test_a_corrupt_archive_fails_only_that_file(ledger, tmp_path, intact):
    """A broken deflate stream fails its file instead of the whole batch."""
    text = FIXTURE.read_text(encoding="utf-8").replace(DOCUMENT_ID, "CORRUPT-1")
    # ``intact`` readable characters, then a deflate block of the reserved type.
    readable = gzip.compress((text + "\n" * intact).encode()) if intact else b""
    broken = gzip.compress(b"")[:10] + b"\xff" * 16
    (tmp_path / "20260807_110000_corrupt.xml.gz").write_bytes(readable + broken)
    delivery(tmp_path, "20260807_120000_good.xml")

    report = sdat_import.import_paths([str(tmp_path)])

    assert [(item.name, item.status) for item in report.files] == [
        ("20260807_110000_corrupt.xml", sdat_import.FAILED),
        ("20260807_120000_good.xml", sdat_import.IMPORTED),
    ]
    assert {item.code for item in report.files[0].diagnostics} == {
        sdat_import.UNREADABLE
    }
    assert ledger.saved == [DOCUMENT_ID]


def test_a_partial_failure_fails_the_command_after_the_good_file(
    ledger, tmp_path, command, capsys
):
    delivery(tmp_path, "20260807_120000_good.xml")
    broken_delivery(tmp_path, "20260807_130000_broken.xml")

    assert command.main([str(tmp_path)]) == 1
    output = capsys.readouterr().out
    assert "20260807_130000_broken.xml\n  Fehler:" in output
    assert _summary(output) == [
        "Dateien: 1 verarbeitet, 0 bereits importiert, 0 übersprungen, 1 fehlerhaft",
        f"Zeilen: neu {FIXTURE_READINGS}, korrigiert 0, unverändert 0",
    ]


def test_a_partial_failure_reports_safe_counts_to_the_scheduler(ledger, tmp_path):
    delivery(tmp_path, "20260807_120000_good.xml")
    broken_delivery(tmp_path, "20260807_130000_broken.xml")

    with pytest.raises(sdat_ingestion.IngestionError) as failure:
        sdat_ingestion._default_import(str(tmp_path))

    assert failure.value.code == "import_failed"
    assert failure.value.counts == {
        "files_imported": 1,
        "files_existing": 0,
        "readings_imported": FIXTURE_READINGS,
    }
    assert "broken" not in str(failure.value)


# ==== Storage setup ====


def test_initialization_runs_once_and_only_for_a_live_import_with_files(
    ledger, tmp_path
):
    delivery(tmp_path, "a.xml", document_id="DOC-A")
    delivery(tmp_path, "b.xml", document_id="DOC-B")
    calls = []

    def initialize():
        calls.append(True)
        return True

    sdat_import.import_paths([str(tmp_path)], dry_run=True, initialize=initialize)
    assert calls == [], "a dry run needs no database"

    report = sdat_import.import_paths([str(tmp_path)], initialize=initialize)
    assert calls == [True]
    assert report.count(sdat_import.IMPORTED) == 2


def test_a_dry_run_reads_every_file_without_storage(monkeypatch, tmp_path):
    use_storage(monkeypatch, RefusingStorage())
    delivery(tmp_path)

    report = sdat_import.import_paths(
        [str(tmp_path)], dry_run=True, initialize=RefusingStorage().init_db
    )

    (outcome,) = report.files
    assert outcome.status == sdat_import.IMPORTED
    assert outcome.document.row_count == FIXTURE_READINGS
    assert outcome.readings is None
    assert (tmp_path / "sample.xml").exists(), "a dry run packs nothing"


def test_unavailable_storage_stops_before_any_file(
    monkeypatch, tmp_path, command, capsys
):
    use_storage(monkeypatch, RefusingStorage())
    path = delivery(tmp_path)

    report = sdat_import.import_paths([str(tmp_path)], initialize=lambda: False)
    assert report.files == ()
    assert report.storage_unavailable
    assert not report.succeeded

    monkeypatch.setattr("database.init_db", lambda: False)
    assert command.main([str(tmp_path)]) == 1
    output = capsys.readouterr().out
    assert output == "DATABASE_URL fehlt oder DB nicht erreichbar.\n"
    assert path.exists()


# ==== Reporter ====


def test_the_reporter_sees_each_outcome_as_it_settles(tmp_path):
    delivery(tmp_path, "20260807_120000_a.xml", document_id="DOC-A")
    broken_delivery(tmp_path, "20260807_130000_b.xml")
    missing = str(tmp_path / "missing")
    events = []

    report = sdat_import.import_paths(
        [missing, str(tmp_path)], dry_run=True, reporter=events.append
    )

    assert events == [
        sdat_import.Diagnostic(sdat_import.MISSING_PATH, missing),
        *report.files,
    ]
    assert report.diagnostics == (events[0],)
    assert [item.status for item in report.files] == [
        sdat_import.IMPORTED,
        sdat_import.FAILED,
    ]
    assert report == sdat_import.import_paths([missing, str(tmp_path)], dry_run=True)
