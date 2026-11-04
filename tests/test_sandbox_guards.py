# SPDX-License-Identifier: AGPL-3.0-or-later
"""Verify the sandbox's process boundary and normal product HTTP behavior."""

import os
import subprocess
import sys
from unittest.mock import patch

import pytest

from tests.native_environment import isolated_environment


def test_worker_network_boundary_allows_unix_and_refuses_internet_sockets():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import socket
from sandbox_network import deny_internet
deny_internet()
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM):
    pass
for family in (socket.AF_INET, socket.AF_INET6, socket.AF_NETLINK):
    try:
        with socket.socket(family, socket.SOCK_STREAM):
            raise AssertionError('non-Unix socket was permitted')
    except PermissionError:
        pass
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_normal_product_does_not_offer_demo_authentication():
    with patch.dict(
        os.environ,
        isolated_environment({"DATABASE_URL": "", "REDIS_URL": "memory://"}),
        clear=True,
    ):
        from app import create_app

        web = create_app(
            {
                "SECRET_KEY": "normal-product-test",
                "APP_BASE_URL": "http://localhost",
                "RATELIMIT_STORAGE_URI": "memory://",
            },
            load_environment=False,
            check_database=False,
        )
        client = web.test_client()
        assert client.get("/sandbox/enter").status_code == 404
        assert client.post("/sandbox/start").status_code == 404
        assert "Simulation · Sonnwil" not in client.get("/login").text


def test_captured_mail_is_published_only_when_complete(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    import sandbox_mail

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENLEG_SANDBOX_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("OPENLEG_SANDBOX_WORKER", "1")
    monkeypatch.setenv("APP_BASE_URL", "http://localhost")
    opened, release = Event(), Event()
    original = sandbox_mail.json.dump

    def pause(*args, **kwargs):
        opened.set()
        assert release.wait(timeout=5)
        return original(*args, **kwargs)

    monkeypatch.setattr(sandbox_mail.json, "dump", pause)
    with ThreadPoolExecutor(max_workers=1) as executor:
        capture = executor.submit(
            sandbox_mail.capture, "fictional@example.org", "Test", "Complete body"
        )
        try:
            assert opened.wait(timeout=5)
            assert sandbox_mail.messages() == []
        finally:
            release.set()
        assert capture.result(timeout=5) is True
    assert sandbox_mail.messages()[0]["body"] == "Complete body"


def test_runner_does_not_enable_debugger_or_load_dotenv(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from flask import Flask

    from scripts import sandbox

    app = Flask(__name__)
    app.extensions["villages"] = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(sandbox, "create_gateway", lambda *args, **kwargs: app)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".flaskenv").write_text("SANDBOX_DOTENV_PROBE=loaded\nFLASK_DEBUG=1\n")
    monkeypatch.setenv("FLASK_DEBUG", "1")
    monkeypatch.delenv("SANDBOX_DOTENV_PROBE", raising=False)
    monkeypatch.setattr(
        "sys.argv", ["sandbox.py", "--directory", str(tmp_path / "private")]
    )
    registered, server = {}, {}
    monkeypatch.setattr(
        sandbox.signal, "signal", lambda sig, handler: registered.update({sig: handler})
    )
    monkeypatch.setattr(
        "werkzeug.serving.run_simple", lambda *args, **kwargs: server.update(kwargs)
    )
    sandbox.main()
    assert server["use_debugger"] is False
    assert "SANDBOX_DOTENV_PROBE" not in os.environ
    assert sandbox.signal.SIGHUP in registered


def test_runner_refuses_a_writable_directory_before_reading_stop_record(
    tmp_path, monkeypatch
):
    import json

    from scripts import sandbox

    (tmp_path / "runner.json").write_text(
        json.dumps({"pid": os.getpid(), "start": sandbox.process_start(os.getpid())})
    )
    tmp_path.chmod(0o777)
    monkeypatch.setattr(
        sandbox.os,
        "kill",
        lambda *args: pytest.fail("must not signal an untrusted record"),
    )
    with pytest.raises((RuntimeError, SystemExit), match="private|writable|owned"):
        sandbox.stop(tmp_path)
