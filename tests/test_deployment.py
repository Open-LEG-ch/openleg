# SPDX-License-Identifier: AGPL-3.0-or-later
"""Config validation tests for deployment artifacts."""

import os
import pytest
import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestCaddyfile:
    """Validate Caddyfile has correct domain blocks."""

    @pytest.fixture(autouse=True)
    def load_caddyfile(self):
        path = os.path.join(PROJECT_ROOT, "Caddyfile")
        with open(path) as f:
            self.content = f.read()

    def test_no_wildcard(self):
        assert "*.openleg.ch" not in self.content

    def test_no_private_gateway_subdomain(self):
        assert "claw.openleg.ch" not in self.content

    def test_has_api_subdomain(self):
        assert "api.openleg.ch" in self.content

    def test_no_insights_subdomain(self):
        assert "insights.openleg.ch" not in self.content

    def test_has_bare_domain(self):
        assert "openleg.ch" in self.content

    def test_www_redirect(self):
        assert "www.openleg.ch" in self.content
        assert "redir" in self.content


class TestDockerCompose:
    """Validate docker-compose.yml structure."""

    @pytest.fixture(autouse=True)
    def load_compose(self):
        path = os.path.join(PROJECT_ROOT, "docker-compose.yml")
        with open(path) as f:
            self.config = yaml.safe_load(f)

    def test_four_services(self):
        assert len(self.config["services"]) == 4
        assert "openclaw" not in self.config["services"]

    def test_flask_healthcheck(self):
        flask = self.config["services"]["flask"]
        assert "healthcheck" in flask

    def test_postgres_healthcheck(self):
        pg = self.config["services"]["postgres"]
        assert "healthcheck" in pg

    def test_caddy_depends_on_flask(self):
        caddy = self.config["services"]["caddy"]
        deps = caddy.get("depends_on", {})
        assert "flask" in deps

    def test_database_url_set(self):
        flask = self.config["services"]["flask"]
        env = flask.get("environment", [])
        db_urls = [e for e in env if "DATABASE_URL" in str(e)]
        assert len(db_urls) > 0


class TestDeployScript:
    """Validate deploy.example.sh public deploy template."""

    @pytest.fixture(autouse=True)
    def load_deploy(self):
        path = os.path.join(PROJECT_ROOT, "deploy.example.sh")
        with open(path) as f:
            self.content = f.read()

    def test_has_required_env_contract(self):
        assert "DEPLOY_HOST" in self.content
        assert "REMOTE_DIR" in self.content

    def test_uses_rsync(self):
        assert "rsync" in self.content
        assert "-az" in self.content

    def test_delete_is_opt_in_and_protects_data(self):
        # --delete must not be unconditional; it is gated behind RSYNC_DELETE.
        assert "rsync -az --delete" not in self.content
        assert "RSYNC_DELETE" in self.content
        # Production-only data must always be excluded from the mirror.
        for protected in ("backups/", "*.sql", "*.sql.gz"):
            assert protected in self.content

    def test_runs_compose_build(self):
        assert "docker compose" in self.content
        assert "--build" in self.content


class TestBlueGreenDeployArtifacts:
    """Validate public-safe blue-green deployment examples."""

    @pytest.fixture(autouse=True)
    def load_artifacts(self):
        with open(os.path.join(PROJECT_ROOT, "docker-compose.blue-green.example.yml")) as f:
            self.compose = yaml.safe_load(f)
        with open(os.path.join(PROJECT_ROOT, "deploy.blue-green.example.sh")) as f:
            self.script = f.read()
        with open(os.path.join(PROJECT_ROOT, "Caddyfile.blue-green.example")) as f:
            self.caddyfile = f.read()

    def test_compose_has_two_flask_slots(self):
        services = self.compose["services"]
        assert "flask-blue" in services
        assert "flask-green" in services
        assert services["flask-blue"]["container_name"] == "openleg-flask-blue"
        assert services["flask-green"]["container_name"] == "openleg-flask-green"

    def test_caddy_mounts_generated_runtime_file(self):
        volumes = self.compose["services"]["caddy"]["volumes"]
        assert "./Caddyfile.blue-green:/etc/caddy/Caddyfile" in volumes

    def test_caddy_template_has_health_checked_placeholder_upstream(self):
        assert "{{UPSTREAM}}" in self.caddyfile
        assert "health_uri /health" in self.caddyfile
        assert "claw.openleg.ch" not in self.caddyfile

    def test_script_never_rebuilds_active_compose_service(self):
        assert "up -d --build" not in self.script
        assert "docker build -t \"$IMAGE_REPO:$inactive\"" in self.script
        assert "--force-recreate \"flask-$inactive\"" in self.script

    def test_script_switches_with_caddy_reload_and_rollback(self):
        assert "caddy reload" in self.script
        assert "ACTIVE_SLOT_FILE" in self.script
        assert "rolling Caddy back" in self.script
        assert "curl -fsS \"$HEALTH_URL\"" in self.script


class TestDockerfile:
    """Validate Dockerfile build config."""

    @pytest.fixture(autouse=True)
    def load_dockerfile(self):
        path = os.path.join(PROJECT_ROOT, "Dockerfile")
        with open(path) as f:
            self.content = f.read()

    def test_uses_gunicorn(self):
        assert "gunicorn" in self.content

    def test_exposes_5000(self):
        assert "EXPOSE 5000" in self.content

    def test_python_311(self):
        assert "python:3.11" in self.content

    def test_gthread_workers(self):
        assert "gthread" in self.content
