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


def test_installed_console_script(operator_http, cli):
    import subprocess
    import sysconfig
    from pathlib import Path

    script = Path(sysconfig.get_path("scripts")) / "openleg-ctl"
    result = subprocess.run(
        [
            str(script),
            "listPeriods",
            "--community-id",
            operator_http.community_id,
            "--json",
        ],
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
