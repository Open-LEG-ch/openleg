# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contract for the metering point enrichment command.

The importer discovers metering points from the E66 data itself, but it cannot
know which building or participant a point belongs to. That mapping arrives as
a CSV exported from the operator's participant list. Re-running the command
with blank columns must never erase what is already stored.
"""

import os
import subprocess
import sys
from pathlib import Path

from scripts import import_metering_points

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "import_metering_points.py"

HEADER = "messpunktnummer,alias,adresse,building_id,community_id,expected_directions\n"


def _run(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
        env=os.environ | {"DATABASE_URL": ""},
        cwd=str(ROOT),
    )


def test_script_follows_house_conventions():
    assert SCRIPT.exists(), "scripts/import_metering_points.py is missing"
    content = SCRIPT.read_text(encoding="utf-8")
    assert content.splitlines()[0] == "# SPDX-License-Identifier: AGPL-3.0-or-later"
    assert "Aufruf:" in content
    assert "def main(" in content
    assert "raise SystemExit(main())" in content


def test_dry_run_reports_rows_without_a_database(tmp_path):
    csv_path = tmp_path / "points.csv"
    csv_path.write_text(
        HEADER + "CH000000000000000000000000000001,Haus 1,Dorfstrasse 1,b-1,leg-1\n",
        encoding="utf-8",
    )

    result = _run(str(csv_path), "--dry-run")

    assert result.returncode == 0, result.stderr
    # The count line, not a bare "1": that matched "Haus 1" and any other digit.
    assert "Messpunkte in der Datei: 1" in result.stdout.splitlines()
    assert "Felder: alias, adresse, building_id, community_id" in result.stdout
    assert "Register aktualisiert" not in result.stdout, "a dry run writes nothing"


def test_dry_run_masks_metering_point_ids(tmp_path):
    point = "CH000000000000000000000000000001"
    csv_path = tmp_path / "points.csv"
    csv_path.write_text(HEADER + f"{point},Haus 1,,,\n", encoding="utf-8")

    result = _run(str(csv_path), "--dry-run")

    assert point not in result.stdout


def test_reads_declared_directions_for_billing(tmp_path):
    csv_path = tmp_path / "points.csv"
    csv_path.write_text(
        HEADER
        + "CH000000000000000000000000000001,Haus 1,,,leg-1,production|consumption\n",
        encoding="utf-8",
    )

    points, errors = import_metering_points._read_points(csv_path)

    assert errors == []
    assert points[0]["expected_directions"] == ["consumption", "production"]


def test_rejects_unknown_declared_direction(tmp_path):
    csv_path = tmp_path / "points.csv"
    csv_path.write_text(
        HEADER + "CH000000000000000000000000000001,Haus 1,,,leg-1,export\n",
        encoding="utf-8",
    )

    points, errors = import_metering_points._read_points(csv_path)

    assert points == []
    assert errors and "expected_directions" in errors[0]


def test_rows_without_a_metering_point_are_reported(tmp_path):
    csv_path = tmp_path / "points.csv"
    csv_path.write_text(HEADER + ",Haus ohne Messpunkt,,,\n", encoding="utf-8")

    result = _run(str(csv_path), "--dry-run")

    assert result.returncode == 1
    assert "ohne Messpunktnummer" in result.stdout


def test_a_row_with_surplus_columns_is_reported_not_crashed(tmp_path):
    # An unquoted comma in an address produces more fields than headers. csv
    # collects the surplus under a single key as a list, which used to reach
    # .strip() and raise. A misaligned row must not be imported either: the
    # mapping it carries decides who gets billed for that metering point.
    csv_path = tmp_path / "points.csv"
    csv_path.write_text(
        HEADER + "CH123,Haus,Dorfstrasse 1, Bern,BLD-1,COMM-1,consumption\n",
        encoding="utf-8",
    )

    result = _run(str(csv_path), "--dry-run")

    assert result.returncode == 1
    assert "Traceback" not in result.stderr, "a bad row must not crash the command"
    assert "Spalten" in result.stdout or "spalten" in result.stdout


def test_missing_file_is_reported(tmp_path):
    result = _run(str(tmp_path / "nope.csv"), "--dry-run")
    assert result.returncode == 1


def test_unknown_header_is_reported(tmp_path):
    csv_path = tmp_path / "points.csv"
    csv_path.write_text("foo,bar\n1,2\n", encoding="utf-8")

    result = _run(str(csv_path), "--dry-run")

    assert result.returncode == 1
    assert "messpunktnummer" in result.stdout.lower()


def test_unsupported_columns_are_reported_before_rows_are_accepted(tmp_path):
    csv_path = tmp_path / "points.csv"
    csv_path.write_text(
        "messpunktnummer,alias,gebaeude_id,building\nCH123,Haus,G-1,Haus A\n",
        encoding="utf-8",
    )

    result = _run(str(csv_path), "--dry-run")

    assert result.returncode == 0
    assert "Nicht unterstützte Spalten: building, gebaeude_id" in result.stderr
    assert "Messpunkte in der Datei: 1" in result.stdout


def test_duplicate_supported_headers_are_rejected(tmp_path):
    csv_path = tmp_path / "points.csv"
    csv_path.write_text(
        "messpunktnummer,building_id,building_id\nCH123,G-1,G-2\n",
        encoding="utf-8",
    )

    result = _run(str(csv_path), "--dry-run")

    assert result.returncode == 1
    assert "Doppelte Spalten: building_id" in result.stdout
    assert "Messpunkte in der Datei: 0" in result.stdout


def test_dry_run_success_is_exact_and_masks_ids_in_stdout_and_stderr(tmp_path):
    point = "CH000000000000000000000000000001"
    csv_path = tmp_path / "points.csv"
    csv_path.write_text(
        HEADER + f"{point},Haus 1,Dorfstrasse 1,b-1,leg-1\n", encoding="utf-8"
    )

    result = _run(str(csv_path), "--dry-run")

    assert result.returncode == 0, result.stderr
    assert "Messpunkte in der Datei: 1" in result.stdout
    assert point not in result.stdout
    assert point not in result.stderr
    assert "...000001" in result.stdout


def test_help_text_uses_swiss_high_german():
    result = _run("--help")
    assert result.returncode == 0, result.stderr
    assert "prüfen" in result.stdout
    assert "pruefen" not in result.stdout
