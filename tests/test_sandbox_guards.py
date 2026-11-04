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
import ctypes
import errno
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
library = ctypes.CDLL("libseccomp.so.2", use_errno=True)
library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
libc = ctypes.CDLL(None, use_errno=True)
for name in (b"io_uring_setup", b"io_uring_enter", b"io_uring_register"):
    number = library.seccomp_syscall_resolve_name(name)
    assert libc.syscall(number, 0, 0, 0, 0, 0, 0) == -1
    assert ctypes.get_errno() == errno.EPERM
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


@pytest.mark.parametrize(
    "missing", ["io_uring_setup", "io_uring_enter", "io_uring_register"]
)
def test_worker_fails_closed_when_an_async_network_rule_is_unavailable(missing):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import ctypes
import sys
import sandbox_network
library = ctypes.CDLL("libseccomp.so.2", use_errno=True)
resolve = library.seccomp_syscall_resolve_name
resolve.argtypes = [ctypes.c_char_p]
resolve.restype = ctypes.c_int
library.seccomp_syscall_resolve_name = lambda name: -1 if name.decode() == sys.argv[1] else resolve(name)
sandbox_network.ctypes.CDLL = lambda *args, **kwargs: library
try:
    sandbox_network.deny_internet()
except RuntimeError as error:
    assert str(error) == 'sandbox network filter unavailable'
else:
    raise AssertionError('worker started without its async network rule')
""",
            missing,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_runner_creates_private_default_parent_under_a_shared_umask(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from flask import Flask

    from scripts import sandbox

    app = Flask(__name__)
    app.extensions["villages"] = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(sandbox, "create_gateway", lambda *args, **kwargs: app)
    monkeypatch.setattr(app, "run", lambda **kwargs: None)
    monkeypatch.setattr(sandbox.signal, "signal", lambda *args: None)
    monkeypatch.setattr("sys.argv", ["sandbox.py"])
    monkeypatch.chdir(tmp_path)
    previous = os.umask(0o002)
    try:
        sandbox.main()
    finally:
        os.umask(previous)
    assert (tmp_path / ".scratch").stat().st_mode & 0o077 == 0


def test_runner_removes_dead_or_reused_stop_records(tmp_path, monkeypatch):
    import json

    from scripts import sandbox

    record = tmp_path / "runner.json"
    record.write_text(json.dumps({"pid": os.getpid(), "start": "stale-start-token"}))
    monkeypatch.setattr(
        sandbox.os, "kill", lambda *args: pytest.fail("must not signal reused PID")
    )
    with pytest.raises(SystemExit, match="no active sandbox runner"):
        sandbox.stop(tmp_path)
    assert not record.exists()


@pytest.mark.parametrize("live", [False, True])
def test_runner_recovers_a_stale_start_record_and_refuses_a_live_one(
    tmp_path, monkeypatch, live
):
    import json
    from types import SimpleNamespace

    from flask import Flask

    from scripts import sandbox

    app = Flask(__name__)
    app.extensions["villages"] = SimpleNamespace(close=lambda: None)
    started = []
    monkeypatch.setattr(
        sandbox, "create_gateway", lambda *args, **kwargs: started.append(True) or app
    )
    monkeypatch.setattr(app, "run", lambda **kwargs: None)
    monkeypatch.setattr(sandbox.signal, "signal", lambda *args: None)
    monkeypatch.setattr("sys.argv", ["sandbox.py", "--directory", str(tmp_path)])
    record = tmp_path / "runner.json"
    identity = {
        "pid": os.getpid(),
        "start": sandbox.process_start(os.getpid()) if live else "old-token",
    }
    record.write_text(json.dumps(identity))
    if live:
        with pytest.raises(SystemExit, match="already active"):
            sandbox.main()
        assert json.loads(record.read_text()) == identity
        assert started == []
    else:
        sandbox.main()
        assert started == [True]
        assert not record.exists()


def test_start_and_stop_cannot_erase_an_incomplete_live_runner_record(
    tmp_path, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from types import SimpleNamespace

    from flask import Flask

    from scripts import sandbox

    app = Flask(__name__)
    app.extensions["villages"] = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(sandbox, "create_gateway", lambda *args, **kwargs: app)
    monkeypatch.setattr(app, "run", lambda **kwargs: None)
    monkeypatch.setattr(sandbox.signal, "signal", lambda *args: None)
    monkeypatch.setattr("sys.argv", ["sandbox.py", "--directory", str(tmp_path)])
    publishing, release = Event(), Event()
    original = sandbox.json.dump

    def pause(*args, **kwargs):
        publishing.set()
        assert release.wait(timeout=5)
        return original(*args, **kwargs)

    monkeypatch.setattr(sandbox.json, "dump", pause)
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(sandbox.main)
        try:
            assert publishing.wait(timeout=5)
            with pytest.raises(SystemExit, match="already active"):
                sandbox.stop(tmp_path)
            with pytest.raises(SystemExit, match="already active"):
                sandbox.main()
            assert (tmp_path / "runner.json").exists()
        finally:
            release.set()
        first.result(timeout=5)


def test_gateway_refuses_a_failed_filter_probe_before_starting_postgres(
    tmp_path, monkeypatch
):
    import sandbox_runtime

    source = tmp_path / "probe-source"
    source.mkdir()
    (source / "sandbox_network.py").write_text(
        "def deny_internet():\n    raise RuntimeError('synthetic unavailable filter')\n"
    )
    monkeypatch.setattr(sandbox_runtime, "SOURCE", source)
    monkeypatch.setattr(
        sandbox_runtime,
        "LocalPostgres",
        lambda *args, **kwargs: pytest.fail(
            "must check filter before starting PostgreSQL"
        ),
    )
    with pytest.raises(RuntimeError, match="sandbox network filter unavailable"):
        sandbox_runtime.create_gateway(tmp_path / "scratch")
    assert not (tmp_path / "scratch").exists()
