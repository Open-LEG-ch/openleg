# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run the production Gunicorn command against PostgreSQL."""

import json
import os
import re
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import pytest
from psycopg2 import sql

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _dockerfile_command():
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    logical_lines = dockerfile.replace("\\\n", "")
    match = re.search(r"^CMD\s+(\[.*\])\s*$", logical_lines, re.MULTILINE)
    assert match, "Dockerfile must define a JSON-form CMD"
    return json.loads(match.group(1))


def _free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@contextmanager
def _temporary_database_url(database_url):
    parsed = urlsplit(database_url)
    admin_url = urlunsplit(parsed._replace(path="/postgres"))
    database_name = f"openleg_gunicorn_{uuid.uuid4().hex}"
    connection = psycopg2.connect(admin_url)
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
            )
        yield urlunsplit(parsed._replace(path=f"/{database_name}"))
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(database_name)
                )
            )
        connection.close()


def _stop_process(process):
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


@pytest.mark.deploy
def test_dockerfile_gunicorn_command_keeps_production_settings():
    command = _dockerfile_command()
    assert command[:2] == ["gunicorn", "wsgi:app"]
    assert command[command.index("--worker-class") + 1] == "gthread"
    assert command[command.index("--workers") + 1] == "2"
    assert command[command.index("--threads") + 1] == "4"
    assert "--preload" in command


@pytest.mark.deploy
@pytest.mark.integration
@pytest.mark.smoke
def test_dockerfile_gunicorn_command_serves_livez():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        pytest.skip("PostgreSQL is required for the Gunicorn smoke test")

    command = _dockerfile_command()
    port = _free_port()
    command[command.index("--bind") + 1] = f"127.0.0.1:{port}"
    with (
        _temporary_database_url(database_url) as isolated_database_url,
        tempfile.TemporaryFile(mode="w+", encoding="utf-8") as gunicorn_log,
    ):
        process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            env=os.environ
            | {
                "APP_BASE_URL": f"http://127.0.0.1:{port}",
                "DATABASE_URL": isolated_database_url,
                "PUBLIC_SITE_URL": f"http://127.0.0.1:{port}",
            },
            stdout=gunicorn_log,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )

        failure = None
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    failure = "Gunicorn exited before /livez was ready"
                    break
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/livez", timeout=1
                    ) as response:
                        assert response.status == 200
                        assert response.read() == b"ok"
                        break
                except (urllib.error.URLError, TimeoutError):
                    time.sleep(0.1)
            else:
                failure = "Gunicorn did not serve /livez within 30 seconds"
        finally:
            _stop_process(process)

        if failure:
            gunicorn_log.seek(0)
            pytest.fail(f"{failure}:\n{gunicorn_log.read()}")
