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

import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALLER_PATH = os.path.join(PROJECT_ROOT, "scripts", "install.sh")


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class TestInstallerScript:
    def setup_method(self):
        self.content = _read(INSTALLER_PATH)

    def test_bash_shebang(self):
        assert self.content.splitlines()[0] == "#!/usr/bin/env bash"

    def test_strict_mode(self):
        assert "set -euo pipefail" in self.content

    def test_checks_docker_present(self):
        assert "command -v docker" in self.content

    def test_generates_strong_secrets(self):
        assert "openssl rand" in self.content
        for key in (
            "POSTGRES_PASSWORD",
            "SECRET_KEY",
            "ADMIN_TOKEN",
            "INTERNAL_TOKEN",
            "CRON_SECRET",
        ):
            assert f"{key}=" in self.content

    def test_never_overwrites_existing_env(self):
        # An existing .env (with real secrets and DB data behind it) must be
        # preserved on re-run. Guarded by a file test, with a visible message.
        assert '-f "$ENV_FILE"' in self.content
        assert "already exists" in self.content

    def test_brings_up_stack(self):
        assert "docker compose" in self.content
        assert "up -d" in self.content

    def test_waits_for_health(self):
        assert "/livez" in self.content

    def test_no_phone_home(self):
        # A self-hosted box sends us nothing. No callback to our domain, no
        # analytics/telemetry beacon anywhere in the installer.
        lower = self.content.lower()
        assert "openleg.ch" not in self.content
        assert "analytics" not in lower
        assert "telemetry" not in lower

    def test_prints_local_url(self):
        assert "http://localhost" in self.content


class TestInstallerRoute:
    def _client(self):
        from flask import Flask

        import self_host

        app = Flask(__name__)
        app.register_blueprint(self_host.self_host_bp)
        return app.test_client()

    def test_served_verbatim(self):
        resp = self._client().get("/install.sh")
        assert resp.status_code == 200
        assert "x-shellscript" in resp.headers["Content-Type"]
        with open(INSTALLER_PATH, "rb") as handle:
            assert resp.data == handle.read()

    def test_served_body_is_a_script(self):
        resp = self._client().get("/install.sh")
        assert resp.data.startswith(b"#!")


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
