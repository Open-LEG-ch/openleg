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

    def test_has_claw_subdomain(self):
        assert "claw.openleg.ch" in self.content

    def test_no_openclaw_subdomain(self):
        assert "openclaw.openleg.ch" not in self.content

    def test_has_api_subdomain(self):
        assert "api.openleg.ch" in self.content

    def test_has_insights_subdomain(self):
        assert "insights.openleg.ch" in self.content

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
