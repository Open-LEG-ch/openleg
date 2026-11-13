"""CLI requests against the real PostgreSQL-backed operator application."""

import json
from contextlib import contextmanager
from threading import Thread

import pytest
from werkzeug.serving import make_server

from openleg_ctl import main
from tests import operator_contract_helpers as helpers
from tests.test_operator_previews import MEMBERSHIP, snapshot

interest_database = helpers.interest_database
operator_http = helpers.operator_http
pytestmark = pytest.mark.integration


@contextmanager
def server(app):
    http = make_server("127.0.0.1", 0, app, threaded=True)
    thread = Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{http.server_port}"
    finally:
        http.shutdown()
        thread.join()
        http.server_close()


@pytest.fixture
def cli(operator_http, monkeypatch, capsys):
    token = operator_http.headers["Authorization"].split()[1]
    monkeypatch.setenv("OPENLEG_TOKEN", token)
    with server(operator_http.app) as url:
        monkeypatch.setenv("OPENLEG_URL", url)

        def call(command, *extra, code=0):
            result = main(
                [
                    command,
                    "--community-id",
                    operator_http.community_id,
                    "--json",
                    *extra,
                ]
            )
            captured = capsys.readouterr()
            assert token not in captured.out + captured.err
            assert result == code, captured
            return json.loads(captured.out if code == 0 else captured.err)

        yield call


def test_reads_and_pagination(operator_http, cli):
    helpers.seed_operator_resources(operator_http)
    with operator_http.db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO billing_periods(community_id,period_start,period_end) VALUES (%s,'2026-02-01','2026-03-01')",
            (operator_http.community_id,),
        )
    first = cli("listPeriods", "--limit", "1")
    second = cli("listPeriods", "--limit", "1", "--cursor", first["next_cursor"])
    assert first["items"] != second["items"]
    assert second["next_cursor"] is None
    for name in (
        "listFormations",
        "listMemberships",
        "listJobs",
        "listCalculatedDeliverys",
        "listInvoices",
        "listCases",
        "listPayments",
    ):
        assert "schema_version" in cli(name)


@pytest.mark.parametrize(
    "command,payload",
    [("submitFormation-preview", {}), ("submitMembershipMutation-preview", MEMBERSHIP)],
)
def test_repeated_previews_preserve_business_rows(operator_http, cli, command, payload):
    helpers.seed_operator_resources(operator_http)
    before = snapshot(operator_http.db)
    arguments = []
    for key, value in payload.items():
        arguments.extend(
            [
                "--" + key.replace("_", "-"),
                json.dumps(value) if isinstance(value, dict) else value,
            ]
        )
    first = cli(command, *arguments)
    assert first["intended_action"] == "prepare_manual_package"
    assert cli(command, *arguments) == first
    after = snapshot(operator_http.db)
    for table in before:
        if table not in {"operator_api_clients", "operator_api_usage"}:
            assert before[table] == after[table], table


def test_api_errors_and_retry_delay(operator_http, cli, monkeypatch):
    assert cli("submitFormation-preview", code=1)["code"] == "invalid_formation"
    assert cli("listPeriods", "--cursor", "bad", code=1)["code"] == "invalid_arguments"
    headers, _ = operator_http.credential_headers(["billing.read"], budget=1)
    monkeypatch.setenv("OPENLEG_TOKEN", headers["Authorization"].split()[1])
    cli("listPeriods")
    error = cli("listPeriods", code=1)
    assert error["code"] == "rate_limited"
    assert error["retry_after"] == "3600"


@pytest.mark.parametrize("install_mode", ["venv", "target"])
def test_installed_console_script(operator_http, cli, tmp_path, install_mode):
    import os
    import subprocess
    import sys
    import sysconfig
    from pathlib import Path

    project = Path(__file__).resolve().parents[1]
    wheels = tmp_path / "wheels"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--no-index",
            "--wheel-dir",
            str(wheels),
            str(project),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    venv = tmp_path / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--system-site-packages", str(venv)],
        check=True,
    )
    python = venv / "bin/python"
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-index",
            str(next(wheels.glob("*.whl"))),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    # Reuse installed runtime dependencies, even when pytest itself is in a venv.
    env = {**os.environ, "PYTHONPATH": sysconfig.get_path("purelib")}
    script = venv / "bin/openleg-ctl"
    if install_mode == "target":
        target = tmp_path / "target"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--no-deps",
                "--no-index",
                "--target",
                str(target),
                str(next(wheels.glob("*.whl"))),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        env["PYTHONPATH"] = str(target) + os.pathsep + env["PYTHONPATH"]
        script = target / "bin/openleg-ctl"
    help_result = subprocess.run(
        [str(script), "--help"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert help_result.returncode == 0, help_result.stderr
    result = subprocess.run(
        [
            str(script),
            "listPeriods",
            "--community-id",
            operator_http.community_id,
            "--json",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["items"] == []
    assert (
        operator_http.headers["Authorization"].split()[1]
        not in result.stdout + result.stderr
    )

    import zipfile

    with zipfile.ZipFile(next(wheels.glob("*.whl"))) as wheel:
        assert (
            wheel.read("openleg_ctl/operator-v1.yaml")
            == (project / "docs/openapi/operator-v1.yaml").read_bytes()
        )


def test_case_response_preview_preserves_business_rows(operator_http, cli):
    from tests.test_invoice_case_agent_postgres import snapshot as case_rows

    ids = helpers.seed_operator_resources(operator_http)
    before = case_rows(operator_http)
    arguments = ["--case-id", str(ids["case_id"]), "--message", "Checked."]
    preview = cli("respondInvoiceCase-preview", *arguments, "--status", "acknowledged")
    assert preview == {
        "schema_version": "operator-api/1",
        "id": ids["case_id"],
        "previous_status": "open",
        "status": "acknowledged",
        "dry_run": True,
    }
    keyed = cli(
        "respondInvoiceCase-preview",
        *arguments,
        "--status",
        "acknowledged",
        "--idempotency-key",
        "case-key-1",
    )
    assert keyed == preview
    assert case_rows(operator_http) == before


def test_payment_preview_sends_idempotency_key(operator_http, cli):
    """The payment preview requires its key, sent as the request header."""
    from tests.test_payment_confirmation_postgres import rows
    from tests.test_payment_confirmation_postgres import (
        seed_operator_resources as seed_payment,
    )

    ids = seed_payment(operator_http)
    before = rows(operator_http)
    preview = cli(
        "confirmPaymentMatch-preview",
        "--entry-id",
        str(ids["entry_id"]),
        "--invoice-id",
        str(ids["invoice_id"]),
        "--idempotency-key",
        "payment-cli",
    )
    assert preview == {
        "schema_version": "operator-api/1",
        "dry_run": True,
        "eligible": True,
        "invoice_id": ids["invoice_id"],
        "lifecycle_state": "paid",
    }
    assert rows(operator_http) == before
