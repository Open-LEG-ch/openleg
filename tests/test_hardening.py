# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for rate limiter Redis + metrics endpoint."""

import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestRateLimiterRedis:
    def test_app_uses_redis_storage(self):
        with open(os.path.join(PROJECT_ROOT, "app.py")) as f:
            content = f.read()
        assert "redis://" in content
        assert "storage_uri='memory://'" not in content

    def test_required_security_extensions_never_fall_back_to_noop(self):
        with open(os.path.join(PROJECT_ROOT, "security_extensions.py")) as f:
            extensions = f.read()
        with open(os.path.join(PROJECT_ROOT, "app.py")) as f:
            application = f.read()

        assert "except ImportError" not in extensions
        assert "lambda view: view" not in extensions
        assert "HAS_SECURITY_LIBS" not in application
        assert "limiter.init_app(application)" in application
        assert "Talisman(" in application


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
