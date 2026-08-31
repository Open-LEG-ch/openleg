# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run the production Gunicorn command against PostgreSQL."""

import json
import os
import re
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

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
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("PostgreSQL is required for the Gunicorn smoke test")

    command = _dockerfile_command()
    port = _free_port()
    command[command.index("--bind") + 1] = f"127.0.0.1:{port}"
    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        env=os.environ
        | {
            "APP_BASE_URL": f"http://127.0.0.1:{port}",
            "PUBLIC_SITE_URL": f"http://127.0.0.1:{port}",
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )

    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                output = process.stdout.read()
                pytest.fail(f"Gunicorn exited before /livez was ready:\n{output}")
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
            pytest.fail("Gunicorn did not serve /livez within 30 seconds")
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5)
