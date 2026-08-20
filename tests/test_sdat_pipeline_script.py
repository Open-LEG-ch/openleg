# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contract tests for scripts/sdat_pipeline.sh.

Die Tests ersetzen den Python-Interpreter durch einen Recorder, damit kein
Netzwerk und keine Datenbank nötig sind. Geprüft wird die Schrittfolge, nicht
das Verhalten der einzelnen Python-Skripte.
"""

import os
import subprocess

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_PATH = os.path.join(PROJECT_ROOT, "scripts", "sdat_pipeline.sh")

RECORDER = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$RECORD_FILE"
printf '%s\\n' "$#" >> "$COUNT_FILE"
exit 0
"""

FAILING_FETCH_RECORDER = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$RECORD_FILE"
printf '%s\\n' "$#" >> "$COUNT_FILE"
if [[ "$*" == *fetch_sdat.py* ]]; then
  echo "Datahub nicht erreichbar" >&2
  exit 1
fi
exit 0
"""

FAILING_COLLECT_RECORDER = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$RECORD_FILE"
printf '%s\\n' "$#" >> "$COUNT_FILE"
if [[ "$*" == *collect_sdat_downloads.py* ]]; then
  echo "Download-Ordner fehlt" >&2
  exit 1
fi
exit 0
"""


@pytest.fixture
def fake_python(tmp_path):
    """Liefert (env, calls, counts) mit einem PYTHON, das seine Argumente mitschreibt.

    ``calls()`` liefert pro Aufruf die zu einer Zeile verbundenen Argumente,
    ``counts()`` die Anzahl Argumente. Nur die Anzahl verrät, ob die Shell ein
    Argument mit Leerzeichen zerlegt hat.
    """

    def _build(body=RECORDER):
        interpreter = tmp_path / "fake_python"
        interpreter.write_text(body)
        interpreter.chmod(0o755)
        record_file = tmp_path / "calls.log"
        record_file.write_text("")
        count_file = tmp_path / "counts.log"
        count_file.write_text("")

        env = dict(os.environ)
        env["PYTHON"] = str(interpreter)
        env["RECORD_FILE"] = str(record_file)
        env["COUNT_FILE"] = str(count_file)

        def _lines(path):
            return [line for line in path.read_text().splitlines() if line.strip()]

        def calls():
            return _lines(record_file)

        def counts():
            return [int(line) for line in _lines(count_file)]

        return env, calls, counts

    return _build


def run_pipeline(env, *args):
    return subprocess.run(
        [SCRIPT_PATH, *args],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_script_exists_and_is_executable():
    assert os.path.exists(SCRIPT_PATH)
    assert os.access(SCRIPT_PATH, os.X_OK)


def test_help_documents_the_fetch_step_and_its_switch():
    result = subprocess.run(
        [SCRIPT_PATH, "--help"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    output = f"{result.stdout}\n{result.stderr}"
    assert "fetch_sdat.py" in output
    assert "--no-fetch" in output


def test_fetch_runs_first_and_writes_into_dest(fake_python):
    env, calls, _counts = fake_python()

    result = run_pipeline(env)

    assert result.returncode == 0, result.stdout + result.stderr
    recorded = calls()
    assert len(recorded) == 3
    assert "scripts/fetch_sdat.py" in recorded[0]
    assert "--out data/sdat" in recorded[0]
    assert "scripts/collect_sdat_downloads.py" in recorded[1]
    assert "scripts/import_sdat.py" in recorded[2]
    assert "data/sdat" in recorded[2]
    # The importer reads *.xml.gz directly, so unpacking the municipality
    # directory only to have the importer pack it again is pure churn.
    assert not any("--source data/sdat" in call for call in recorded), (
        "the in-directory unpack step must be gone"
    )


def test_fetch_targets_the_given_dest(fake_python):
    env, calls, _counts = fake_python()

    result = run_pipeline(env, "data/koeniz")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "--out data/koeniz" in calls()[0]
    assert "data/koeniz" in calls()[2]


def test_no_fetch_skips_the_download_step(fake_python):
    env, calls, _counts = fake_python()

    result = run_pipeline(env, "--no-fetch")

    assert result.returncode == 0, result.stdout + result.stderr
    recorded = calls()
    assert len(recorded) == 2
    assert not any("fetch_sdat.py" in call for call in recorded)


def test_no_fetch_is_not_forwarded_to_the_import(fake_python):
    env, calls, _counts = fake_python()

    result = run_pipeline(env, "--no-fetch", "--dry-run")

    assert result.returncode == 0, result.stdout + result.stderr
    import_call = calls()[-1]
    assert "--no-fetch" not in import_call
    assert "--dry-run" in import_call


def test_no_fetch_works_after_the_dest_argument(fake_python):
    env, calls, _counts = fake_python()

    result = run_pipeline(env, "data/koeniz", "--no-fetch")

    assert result.returncode == 0, result.stdout + result.stderr
    recorded = calls()
    assert len(recorded) == 2
    assert "data/koeniz" in recorded[-1]
    assert "--no-fetch" not in recorded[-1]


def test_import_args_are_forwarded(fake_python):
    env, calls, _counts = fake_python()

    result = run_pipeline(env, "data/schwarzenburg", "--dry-run", "--force")

    assert result.returncode == 0, result.stdout + result.stderr
    import_call = calls()[-1]
    assert "--dry-run" in import_call
    assert "--force" in import_call


def test_arguments_with_spaces_survive_the_no_fetch_filter(fake_python):
    # Die Filterschleife baut $@ neu auf. Das darf Argumente nicht zerlegen.
    env, calls, counts = fake_python()

    result = run_pipeline(env, "--no-fetch", "--label", "Sommer 2026")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "--label Sommer 2026" in calls()[-1]
    # import_sdat.py <dest> --label "Sommer 2026" sind genau vier Argumente.
    # Bei zerlegtem Argument wären es fünf.
    assert counts()[-1] == 4


def test_failed_fetch_warns_but_still_imports(fake_python):
    env, calls, _counts = fake_python(FAILING_FETCH_RECORDER)

    result = run_pipeline(env)

    assert result.returncode == 0, result.stdout + result.stderr
    output = f"{result.stdout}\n{result.stderr}"
    assert "--no-fetch" in output or "Warnung" in output
    recorded = calls()
    assert len(recorded) == 3
    assert "scripts/import_sdat.py" in recorded[-1]


def test_failed_collector_warns_but_still_imports(fake_python):
    env, calls, _counts = fake_python(FAILING_COLLECT_RECORDER)

    result = run_pipeline(env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Warnung" in result.stderr
    recorded = calls()
    assert len(recorded) == 3
    assert "scripts/collect_sdat_downloads.py" in recorded[1]
    assert "scripts/import_sdat.py" in recorded[2]


def test_missing_interpreter_still_fails_fast(tmp_path):
    env = dict(os.environ)
    env["PYTHON"] = str(tmp_path / "does-not-exist")

    result = run_pipeline(env)

    assert result.returncode == 2
    assert "Virtualenv" in result.stderr


def test_german_prose_uses_swiss_high_german():
    """Source contract: German prose in scripts/sdat_pipeline.sh must use real umlauts."""
    with open(SCRIPT_PATH, encoding="utf-8") as f:
        text = f.read()
    forbidden = (
        "Fuehrt",
        "Frueher",
        "zusaetzlicher",
        "Schlaegt",
        "laesst",
        "fuer",
        "ueberspringen",
        "noetig",
        "ueberschreiben",
        "pruefen",
        "uebersprungen",
        "ueberspringt",
    )
    found = [word for word in forbidden if word in text]
    assert not found, f"Found forbidden umlaut transliterations: {found}"
