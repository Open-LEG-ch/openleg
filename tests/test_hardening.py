"""Tests for rate limiter Redis + metrics endpoint."""
import os
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestRateLimiterRedis:
    def test_app_uses_redis_storage(self):
        with open(os.path.join(PROJECT_ROOT, "app.py")) as f:
            content = f.read()
        assert "redis://" in content
        assert "storage_uri='memory://'" not in content


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
