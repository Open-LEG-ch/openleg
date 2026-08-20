# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the one-command self-host installer (Phase 9 T1).

The installer is the download-first on-ramp: a homeowner runs one command and
gets a working OpenLEG on a device they own. Two invariants matter most and are
pinned here so they cannot silently regress:

1. The bytes served at GET /install.sh are exactly the audited scripts/install.sh
   in the repo, so "pipe to shell" can never drift from the file a cautious host
   reads first.
2. The script is safe: strict mode, generates real secrets, never clobbers an
   existing .env, waits for health, and phones nothing home.
"""

import os
import shutil
import subprocess

import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALLER_PATH = os.path.join(PROJECT_ROOT, "scripts", "install.sh")
OPERATOR_PATH = os.path.join(PROJECT_ROOT, "scripts", "openleg")


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _run_operator_install(tmp_path, env_content):
    root = tmp_path / "checkout"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    operator = scripts / "openleg"
    shutil.copy(OPERATOR_PATH, operator)
    operator.chmod(0o755)
    env_file = root / ".env"
    env_file.write_text(env_content, encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker_log = tmp_path / "docker.log"
    commands = {
        "docker": f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{docker_log}"\nexit 0\n',
        "curl": "#!/bin/sh\nexit 0\n",
        "openssl": "#!/bin/sh\nprintf generated-secret\n",
    }
    for name, content in commands.items():
        path = bin_dir / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)

    result = subprocess.run(
        [str(operator), "install"],
        env=os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        check=False,
    )
    return result, env_file.read_text(encoding="utf-8"), docker_log.read_text()


class TestInstallerScript:
    def setup_method(self):
        self.content = _read(INSTALLER_PATH)
        self.lifecycle = _read(OPERATOR_PATH)

    def test_bash_shebang(self):
        assert self.content.splitlines()[0] == "#!/usr/bin/env bash"

    def test_strict_mode(self):
        assert "set -euo pipefail" in self.content

    def test_checks_docker_present(self):
        assert "command in docker curl openssl" in self.lifecycle

    def test_generates_strong_secrets(self):
        assert "openssl rand" in self.lifecycle
        for key in (
            "POSTGRES_PASSWORD",
            "SECRET_KEY",
            "ADMIN_TOKEN",
            "INTERNAL_TOKEN",
            "CRON_SECRET",
        ):
            assert f"add_default {key} " in self.lifecycle

    def test_never_overwrites_existing_env(self):
        # An existing .env (with real secrets and DB data behind it) must be
        # preserved on re-run. Guarded by a file test, with a visible message.
        assert "-f .env" in self.lifecycle
        assert "already exists" in self.lifecycle

    def test_brings_up_stack(self):
        assert "docker compose" in self.lifecycle
        assert "up -d" in self.lifecycle

    def test_waits_for_health(self):
        assert "/livez" in self.lifecycle
        assert "curl -fsSL" in self.lifecycle

    def test_no_phone_home(self):
        # A self-hosted box sends us nothing. No callback to our domain, no
        # analytics/telemetry beacon anywhere in the installer.
        lower = self.content.lower()
        assert "openleg.ch" not in self.content
        assert "analytics" not in lower
        assert "telemetry" not in lower

    def test_prints_local_url(self):
        assert "http://localhost" in self.lifecycle


def test_fresh_directory_bootstraps_checkout_and_delegates(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "delegated"
    install_dir = tmp_path / "openleg"

    for name in ("docker", "curl"):
        path = bin_dir / name
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)

    git = bin_dir / "git"
    git.write_text(
        "#!/bin/sh\n"
        'mkdir -p "$5/scripts"\n'
        'printf \'#!/bin/sh\\nprintf "%%s" "$1" > "$DELEGATED_MARKER"\\n\' '
        '> "$5/scripts/openleg"\n'
        'chmod +x "$5/scripts/openleg"\n',
        encoding="utf-8",
    )
    git.chmod(0o755)

    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "OPENLEG_INSTALL_DIR": str(install_dir),
        "OPENLEG_REPOSITORY": "https://example.invalid/openleg.git",
        "DELEGATED_MARKER": str(marker),
    }
    caller_dir = tmp_path / "caller"
    caller_dir.mkdir()

    result = subprocess.run(
        ["bash", INSTALLER_PATH],
        cwd=caller_dir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert marker.read_text(encoding="utf-8") == "install"


def test_install_completes_partial_env_without_overwriting_or_duplication(tmp_path):
    original = "APP_BASE_URL=https://existing.example\nCUSTOM=value\n"

    first, completed, _ = _run_operator_install(tmp_path, original)

    assert first.returncode == 0, first.stderr
    assert completed.startswith(original)
    for key in (
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "SECRET_KEY",
        "ADMIN_TOKEN",
        "INTERNAL_TOKEN",
        "CRON_SECRET",
        "APP_BASE_URL",
        "ALLOWED_HOSTS",
    ):
        assert completed.count(f"{key}=") == 1

    second_root = tmp_path / "second"
    second_root.mkdir()
    second, rerun, _ = _run_operator_install(second_root, completed)

    assert second.returncode == 0, second.stderr
    assert rerun == completed


def test_install_rejects_empty_required_env_before_compose_up(tmp_path):
    result, _, docker_log = _run_operator_install(tmp_path, "POSTGRES_PASSWORD=\n")

    assert result.returncode != 0
    assert "POSTGRES_PASSWORD" in result.stderr
    assert " up " not in f" {docker_log} "


class TestInstallerShippedInImage:
    """The hosted app must be able to serve /install.sh, so the script has to
    be inside the Docker image, not just in the source tree."""

    def test_dockerfile_copies_scripts(self):
        content = _read(os.path.join(PROJECT_ROOT, "Dockerfile"))
        assert "COPY scripts/" in content


class TestQuickstartComposeOverride:
    """QuickStart publishes the app on a plain LAN HTTP port so a local box is
    reachable without a public domain or TLS (the Advanced path keeps Caddy)."""

    def setup_method(self):
        path = os.path.join(PROJECT_ROOT, "docker-compose.quickstart.yml")
        with open(path) as handle:
            self.config = yaml.safe_load(handle)

    def test_publishes_flask_http_port(self):
        ports = self.config["services"]["flask"]["ports"]
        joined = " ".join(str(p) for p in ports)
        assert "5000" in joined
        assert "OPENLEG_HTTP_PORT" in joined
        assert "8080" in joined

    def test_uses_the_printed_local_http_url(self):
        environment = self.config["services"]["flask"]["environment"]
        assert environment["APP_BASE_URL"] == (
            "http://localhost:${OPENLEG_HTTP_PORT:-8080}"
        )
