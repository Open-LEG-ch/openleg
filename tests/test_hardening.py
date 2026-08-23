# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for rate limiter Redis + metrics endpoint."""

import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestRateLimiterRedis:
    def test_app_uses_redis_storage(self):
        """Asserted on the built config, not on one module's source text.

        The default moved into app_config.build_config (#336); the contract is
        that the limiter talks to Redis unless the environment says otherwise.
        """
        import app_config

        assert app_config.build_config({})["RATELIMIT_STORAGE_URI"].startswith(
            "redis://"
        )
        assert (
            app_config.build_config({"REDIS_URL": "redis://cache:6379/2"})[
                "RATELIMIT_STORAGE_URI"
            ]
            == "redis://cache:6379/2"
        )

    def test_required_security_extensions_never_fall_back_to_noop(self):
        with open(os.path.join(PROJECT_ROOT, "security_extensions.py")) as f:
            extensions = f.read()
        with open(os.path.join(PROJECT_ROOT, "app.py")) as f:
            application = f.read()

        assert "except ImportError" not in extensions
        assert "lambda view: view" not in extensions
        assert "if limiter else lambda" not in application
        assert "HAS_SECURITY_LIBS" not in application
        assert "limiter.init_app(application)" in application
        assert "Talisman(" in application

    def test_required_security_extensions_are_enforced_at_runtime(self, monkeypatch):
        import app as app_module
        import municipality

        monkeypatch.setattr(
            municipality.db, "get_municipality_by_admin_email", lambda _email: None
        )
        application = app_module.create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "hardening-runtime-test-key",
                "APP_BASE_URL": "http://localhost:5003",
                "RATELIMIT_STORAGE_URI": "memory://",
            },
            load_environment=False,
            check_database=False,
        )
        app_module.limiter.reset()
        client = application.test_client()

        responses = [
            client.post(
                "/gemeinde/access/request",
                data={"email": "unknown@example.ch"},
                environ_overrides={"REMOTE_ADDR": "192.0.2.82"},
            )
            for _attempt in range(6)
        ]

        assert application.config["RATELIMIT_STORAGE_URI"] == "memory://"
        assert [response.status_code for response in responses] == [200] * 5 + [429]
        csp = responses[0].headers.get("Content-Security-Policy", "")
        assert "default-src 'self'" in csp
        assert "script-src 'self'" in csp


class TestMetricsEndpoint:
    def test_metrics_route_exists(self):
        with open(os.path.join(PROJECT_ROOT, "app.py")) as f:
            content = f.read()
        assert "/metrics" in content


class TestDockerComposeRedis:
    def test_flask_has_redis_url(self):
        with open(os.path.join(PROJECT_ROOT, "docker-compose.yml")) as f:
            content = f.read()
        assert "REDIS_URL" in content
