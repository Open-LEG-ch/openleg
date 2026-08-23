# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contract for the SDAT import command.

The importer runs unattended against an SFTP drop, so it has to skip files it
has already seen, skip the E31 siblings that share the directory, survive one
bad file without abandoning the batch, and never print a full metering point
id. --dry-run must work with no database at all, which keeps the happy path
testable in CI.
"""

import builtins
import importlib.util
import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import dotenv
import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "import_sdat.py"
FIXTURE = ROOT / "tests" / "fixtures" / "sdat_e66_sample.xml"

POINT_ONE = "CH000000000000000000000000000001"

E31_DOCUMENT = """<?xml version="1.0" encoding="utf-8"?>
<rsm:AggregatedMeteredData_13 xmlns:rsm="http://www.strom.ch">
  <rsm:AggregatedMeteredData_HeaderInformation>
    <rsm:InstanceDocument>
      <rsm:DocumentID>AGG-1</rsm:DocumentID>
      <rsm:DocumentType listAgencyID="260">
        <rsm:ebIXCode>E31</rsm:ebIXCode>
      </rsm:DocumentType>
    </rsm:InstanceDocument>
  </rsm:AggregatedMeteredData_HeaderInformation>
</rsm:AggregatedMeteredData_13>"""


def _run(*args, env=None):
    environment = os.environ | {"DATABASE_URL": ""} | (env or {})
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
        cwd=str(ROOT),
    )


def _snapshot_tree(root):
    snapshot = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            snapshot[rel] = path.read_bytes()
    return snapshot


def test_import_has_no_dotenv_or_database_side_effect(monkeypatch):
    events = []

    def _tracking_load_dotenv(*args, **kwargs):
        events.append("dotenv")
        return False

    monkeypatch.setattr(dotenv, "load_dotenv", _tracking_load_dotenv)

    real_import = builtins.__import__

    def _tracking_import(name, *args, **kwargs):
        if name == "database":
            events.append("database")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _tracking_import)

    spec = importlib.util.spec_from_file_location("import_sdat_fresh", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert events == []


def test_cli_entrypoint_loads_dotenv(monkeypatch):
    calls = []

    def _tracking_load_dotenv(*args, **kwargs):
        calls.append("dotenv")
        return False

    monkeypatch.setattr(dotenv, "load_dotenv", _tracking_load_dotenv)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--help"])

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(str(SCRIPT), run_name="__main__")

    assert exc_info.value.code == 0
    assert calls == ["dotenv"]


# ==== Script conventions ====


def test_script_exists_and_follows_house_conventions():
    assert SCRIPT.exists(), "scripts/import_sdat.py is missing"
    content = SCRIPT.read_text(encoding="utf-8")
    lines = content.splitlines()
    assert lines[0] == "# SPDX-License-Identifier: AGPL-3.0-or-later"
    assert "Aufruf:" in content, "the docstring needs a German usage block"
    assert "def main(" in content
    assert "raise SystemExit(main())" in content


def test_script_offers_force_and_dry_run():
    content = SCRIPT.read_text(encoding="utf-8")
    assert "argparse" in content
    assert "--force" in content
    assert "--dry-run" in content


# ==== Dry run ====


def test_dry_run_parses_without_a_database():
    result = _run(str(FIXTURE), "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "Messpunkte 2" in result.stdout
    assert "Zeilen 9" in result.stdout


def test_dry_run_reports_summary_without_write_totals():
    result = _run(str(FIXTURE), "--dry-run")
    assert result.returncode == 0
    # main() prints the row totals only when it actually wrote them, so their
    # absence is the signal. The previous check sliced the output on "zeilen"
    # and asserted against the part before it, which passed either way.
    assert "Zeilen: neu" not in result.stdout
    assert "bereits importiert" in result.stdout, "the file summary still prints"


def test_output_masks_metering_point_ids():
    result = _run(str(FIXTURE), "--dry-run")
    assert POINT_ONE not in result.stdout, (
        "metering point ids are personal data once joined to the registry"
    )


# ==== File selection ====


def test_directory_argument_finds_the_xml_files(tmp_path):
    shutil.copy(FIXTURE, tmp_path / "sample.xml")
    result = _run(str(tmp_path), "--dry-run")
    assert result.returncode == 0
    assert "Messpunkte 2" in result.stdout


def test_e31_sibling_is_skipped_not_failed(tmp_path):
    (tmp_path / "aggregate.xml").write_text(E31_DOCUMENT, encoding="utf-8")
    shutil.copy(FIXTURE, tmp_path / "sample.xml")

    result = _run(str(tmp_path), "--dry-run")

    assert result.returncode == 0, result.stderr
    assert "Messpunkte 2" in result.stdout
    assert "1 übersprungen" in result.stdout


def test_e31_sibling_prints_nothing(tmp_path):
    # E31 Geschwisterdateien liegen bei jedem Download dabei. Eine Zeile pro
    # Datei macht die Ausgabe unlesbar, darum bleiben sie still.
    (tmp_path / "aggregate.xml").write_text(E31_DOCUMENT, encoding="utf-8")
    shutil.copy(FIXTURE, tmp_path / "sample.xml")

    result = _run(str(tmp_path), "--dry-run")

    assert "aggregate.xml" not in result.stdout
    assert "kein E66" not in result.stdout


def test_foreign_xml_is_skipped_like_an_e31(tmp_path):
    (tmp_path / "foreign.xml").write_text("<not-sdat/>", encoding="utf-8")
    result = _run(str(tmp_path), "--dry-run")
    assert result.returncode == 0
    assert "foreign.xml" not in result.stdout
    assert "1 übersprungen" in result.stdout


def test_malformed_e66_fails_without_abandoning_the_batch(tmp_path):
    # Declares itself E66 and then breaks. Skipping this silently would hide a
    # real delivery problem, so it has to be reported as a failure.
    truncated = FIXTURE.read_text(encoding="utf-8")[:2000]
    (tmp_path / "broken.xml").write_text(truncated, encoding="utf-8")
    shutil.copy(FIXTURE, tmp_path / "sample.xml")

    result = _run(str(tmp_path), "--dry-run")

    assert result.returncode == 1, "a malformed E66 must be reported as a failure"
    assert "Messpunkte 2" in result.stdout, "the good file must still be processed"


def test_missing_path_is_reported():
    result = _run(str(ROOT / "does-not-exist"), "--dry-run")
    assert result.returncode == 1
    assert "Pfad nicht gefunden" in result.stdout
    assert "Traceback" not in result.stderr


def test_dry_run_preserves_every_relative_path_and_byte(tmp_path):
    drop = tmp_path / "drop" / "2026-08-16"
    drop.mkdir(parents=True)
    e66 = drop / "sdat_e66_sample.xml"
    shutil.copy(FIXTURE, e66)
    (drop / "aggregate.xml").write_text(E31_DOCUMENT, encoding="utf-8")
    (drop / "readme.txt").write_text("Nicht SDAT", encoding="utf-8")

    before = _snapshot_tree(drop)
    result = _run(str(drop), "--dry-run")
    after = _snapshot_tree(drop)

    assert result.returncode == 0, result.stderr
    assert before == after, "Dry-run darf keine Dateien ändern, löschen oder hinzufügen"
    assert "Messpunkte 2" in result.stdout
    assert "1 übersprungen" in result.stdout
    assert "Zeilen: neu" not in result.stdout


def test_help_text_uses_swiss_high_german():
    result = _run("--help")
    assert result.returncode == 0, result.stderr
    assert "prüfen" in result.stdout
    assert "pruefen" not in result.stdout


# ==== Database-backed behaviour ====


@pytest.mark.integration
def test_second_run_reports_no_changes(tmp_path):
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("needs a live database")

    # A copy, never the tracked fixture: a successful import packs its input and
    # removes the plain file, which would delete the fixture out from under every
    # later test in the suite.
    delivery = tmp_path / "sdat_e66_sample.xml"
    shutil.copy(FIXTURE, delivery)

    first = _run(str(delivery), env={"DATABASE_URL": os.environ["DATABASE_URL"]})
    assert first.returncode == 0, first.stderr
    assert not delivery.exists(), "the import packs what it stored"

    second = _run(
        str(tmp_path), "--force", env={"DATABASE_URL": os.environ["DATABASE_URL"]}
    )
    assert second.returncode == 0, second.stderr
    assert "neu 0" in second.stdout
    assert "korrigiert 0" in second.stdout
    assert FIXTURE.exists(), (
        "the suite must not consume its own fixture; every later test reads it"
    )


@pytest.mark.integration
def test_a_changed_reading_is_reported_as_a_correction(tmp_path):
    """The guard that decides new from corrected, executed rather than grepped.

    `store/metering.py` classifies a row with an `IS DISTINCT FROM` list. The
    unit tests around it check that the SQL text mentions the right columns and
    feed a hand-authored result list into a mocked `execute_values`, so which
    rows Postgres would really call corrected is asserted nowhere. The existing
    integration test only reimports an identical file, which proves the opposite
    direction: nothing changed, nothing reported.

    Point 1's first consumption interval is total 0.100 = grid 0.060 + community
    0.040. Moving community to 0.041 touches that column alone and leaves the
    balance 0.001 kWh out, inside E66_BALANCE_TOLERANCE_KWH, so the row still
    imports. Drop `community_kwh` from the guard and this correction becomes
    invisible.
    """
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("needs a live database")

    original = FIXTURE.read_text(encoding="utf-8")
    community_first_interval = """    <rsm:Observation>
      <rsm:Position><rsm:Sequence>1</rsm:Sequence></rsm:Position>
      <rsm:Volume>0.040</rsm:Volume>
    </rsm:Observation>"""
    assert community_first_interval in original, "fixture shape changed"
    corrected_text = original.replace(
        community_first_interval,
        community_first_interval.replace("0.040", "0.041"),
        1,
    )
    assert corrected_text != original

    # Establish a known baseline so the test is independent of order.
    baseline = tmp_path / "baseline" / "sdat_e66_sample.xml"
    baseline.parent.mkdir()
    shutil.copy(FIXTURE, baseline)

    try:
        base = _run(
            str(baseline),
            "--force",
            env={"DATABASE_URL": os.environ["DATABASE_URL"]},
        )
        assert base.returncode == 0, base.stderr

        correction = tmp_path / "second" / "sdat_e66_sample.xml"
        correction.parent.mkdir()
        correction.write_text(corrected_text, encoding="utf-8")

        second = _run(
            str(correction.parent),
            "--force",
            env={"DATABASE_URL": os.environ["DATABASE_URL"]},
        )

        assert second.returncode == 0, second.stderr
        assert "neu 0" in second.stdout, second.stdout
        assert "korrigiert 1" in second.stdout, second.stdout
    finally:
        # Leave the database holding the pristine 0.040 for whatever runs next.
        cleanup = tmp_path / "cleanup" / "sdat_e66_sample.xml"
        cleanup.parent.mkdir(exist_ok=True)
        shutil.copy(FIXTURE, cleanup)
        restored = _run(
            str(cleanup),
            "--force",
            env={"DATABASE_URL": os.environ["DATABASE_URL"]},
        )

    # Checked here rather than inside the finally on purpose: a failed restore
    # must be visible, but asserting it while an exception is in flight would
    # replace the real failure with this one.
    assert restored.returncode == 0, restored.stderr
    assert FIXTURE.exists(), "the suite must not consume its own fixture"
