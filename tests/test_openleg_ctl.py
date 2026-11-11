"""Contract command coverage at the public parser boundary."""

import pytest

from tests.test_operator_contract import load_contract


def test_commands_cover_contract_and_exclude_submissions(capsys):
    from openleg_ctl import main

    spec = load_contract()
    expected = []
    for methods in spec["paths"].values():
        for method, op in methods.items():
            dry = any(p.get("name") == "dry_run" for p in op.get("parameters", []))
            name = op["operationId"] + ("-preview" if dry else "")
            if method == "get" or dry:
                expected.append(name)
                assert main([name, "--help"]) == 0
                assert "--json" in capsys.readouterr().out
            if method != "get":
                assert main([op["operationId"], "--help"]) != 0
                capsys.readouterr()
    assert len(expected) == 10


@pytest.mark.parametrize(
    "extra",
    [["--token", "olk_secret"], ["--limit", "olk_secret"], ["--dry-run", "false"]],
)
def test_rejects_unsafe_flags_without_echo(extra, monkeypatch, capsys):
    from openleg_ctl import main

    monkeypatch.setenv("OPENLEG_TOKEN", "olk_secret")
    assert main(["listPeriods", "--community-id", "leg", *extra]) != 0
    output = capsys.readouterr()
    assert "olk_secret" not in output.out + output.err
    assert "invalid_arguments" in output.err


def test_network_failure(monkeypatch, capsys):
    import socket

    from openleg_ctl import main

    monkeypatch.setenv("OPENLEG_TOKEN", "olk_secret")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        assert (
            main(["listPeriods", "--community-id", "leg", "--url", url, "--json"]) != 0
        )
    assert "network_error" in capsys.readouterr().err


def test_upstream_cannot_echo_token(monkeypatch, capsys):
    from flask import Flask, jsonify

    from openleg_ctl import main
    from tests.test_openleg_ctl_postgres import server

    app = Flask(__name__)
    token = "olk_secret"
    monkeypatch.setenv("OPENLEG_TOKEN", token)

    @app.get("/api/operator/v1/communities/leg/billing/periods")
    def response():
        return jsonify(items=[{"message": token}])

    with server(app) as url:
        assert (
            main(["listPeriods", "--community-id", "leg", "--url", url, "--json"]) == 0
        )
    output = capsys.readouterr()
    assert token not in output.out + output.err
    assert "[redacted]" in output.out


@pytest.mark.parametrize(
    "command", ["submitFormation-preview", "submitMembershipMutation-preview"]
)
def test_preview_has_no_dry_run_override(command, capsys):
    from openleg_ctl import main

    assert main([command, "--community-id", "leg", "--dry-run", "false"]) != 0
    assert "invalid_arguments" in capsys.readouterr().err


def test_redirect_does_not_forward_credentials(monkeypatch, capsys):
    from flask import Flask, redirect

    from openleg_ctl import main
    from tests.test_openleg_ctl_postgres import server

    app = Flask(__name__)
    followed = []

    @app.get("/api/operator/v1/communities/leg/billing/periods")
    def response():
        return redirect("/leak")

    @app.get("/leak")
    def leak():
        followed.append(True)
        return "{}"

    monkeypatch.setenv("OPENLEG_TOKEN", "olk_secret")
    with server(app) as url:
        assert main(["listPeriods", "--community-id", "leg", "--url", url]) != 0
    assert not followed
    output = capsys.readouterr()
    assert "olk_secret" not in output.out + output.err
