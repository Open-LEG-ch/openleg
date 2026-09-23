# SPDX-License-Identifier: AGPL-3.0-or-later
"""TDD tests for tenant cache backed by Redis via cache.py."""

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from tests.test_dashboard_access_routes import (
    app_module as dashboard_app_module,  # noqa: F401
)


@pytest.fixture
def mock_redis():
    mock = MagicMock()
    mock.get.return_value = None
    mock.set.return_value = True
    mock.setex.return_value = True
    mock.delete.return_value = True
    with patch("cache._get_redis", return_value=mock):
        yield mock


@pytest.fixture(autouse=True)
def clear_tenant_cache():
    """Clear in-memory fallback between tests."""
    import tenant

    tenant._tenant_cache.clear()
    yield
    tenant._tenant_cache.clear()


class TestTenantRedisCache:
    """Tenant config fetched from Redis, falls back to DB on miss."""

    def test_cache_hit_skips_db(self, mock_redis):
        from tenant import get_tenant_config

        config = {"territory": "baden", "city_name": "Baden", "active": True}
        mock_redis.get.return_value = json.dumps(config).encode()
        result = get_tenant_config("baden", db=None)
        assert result["territory"] == "baden"
        assert result["city_name"] == "Baden"
        assert result["utility_name"] == "VNB"
        assert result["map_center_lat"] == 46.8
        assert result["map_zoom"] == 7
        mock_redis.get.assert_called_with("openleg:tenant:baden")

    def test_cache_miss_falls_back_to_db(self, mock_redis):
        mock_redis.get.return_value = None
        mock_db = MagicMock()
        conn_mock = MagicMock()
        cur_mock = MagicMock()
        cur_mock.fetchone.return_value = {
            "territory": "baden",
            "utility_name": "AEW",
            "primary_color": "#c7021a",
            "secondary_color": "#4338ca",
            "contact_email": "",
            "contact_phone": "",
            "legal_entity": "",
            "dso_contact": "",
            "active": True,
            "config": {},
        }
        conn_mock.__enter__ = MagicMock(return_value=conn_mock)
        conn_mock.__exit__ = MagicMock(return_value=False)
        conn_mock.cursor.return_value.__enter__ = MagicMock(return_value=cur_mock)
        conn_mock.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_db.get_connection.return_value = conn_mock

        from tenant import get_tenant_config

        result = get_tenant_config("baden", db=mock_db)
        assert result["territory"] == "baden"
        assert result["utility_name"] == "AEW"
        # Should have written to Redis with the new signature and TTL
        mock_redis.set.assert_called_once_with(
            "openleg:tenant:baden", json.dumps(result), ex=300
        )
        mock_redis.setex.assert_not_called()

    def test_memory_cache_fills_missing_fields_without_replacing_regional_values(
        self, mock_redis
    ):
        import tenant

        tenant._tenant_cache["baden"] = (
            {"territory": "baden", "utility_name": "AEW"},
            time.time(),
        )
        result = tenant.get_tenant_config("baden")
        assert result["utility_name"] == "AEW"
        assert result["map_center_lat"] == 46.8
        assert result["map_zoom"] == 7

    def test_cache_miss_no_db_returns_default(self, mock_redis):
        from tenant import get_tenant_config

        result = get_tenant_config("zurich", db=None)
        assert result["territory"] == "zurich"
        assert result["city_name"] == "Zürich"

    def test_redis_down_still_works(self):
        """If Redis is completely down, tenant resolution still works."""
        with patch("cache._get_redis", side_effect=Exception("Connection refused")):
            from tenant import get_tenant_config

            result = get_tenant_config("zurich", db=None)
            assert result["territory"] == "zurich"


class TestTenantInvalidation:
    """Invalidation clears Redis key."""

    def test_invalidate_single(self, mock_redis):
        from tenant import invalidate_cache

        invalidate_cache("baden")
        mock_redis.delete.assert_called_with("openleg:tenant:baden")

    def test_invalidate_all(self, mock_redis):
        from tenant import invalidate_cache

        mock_redis.keys.return_value = [b"openleg:tenant:a", b"openleg:tenant:b"]
        invalidate_cache(None)
        mock_redis.keys.assert_called_with("openleg:tenant:*")


class TestAppDrivesWithCacheUnavailable:
    """Driving the real app with a down cache must not error any request (#529)."""

    def test_requests_still_serve_when_the_cache_is_down(
        self,
        monkeypatch,
        dashboard_app_module,  # noqa: F811
    ):
        def raise_down(key):
            raise ConnectionError("redis down")

        monkeypatch.setattr("cache.cache_get", raise_down)
        monkeypatch.setattr("cache.cache_set", lambda *a, **k: None)

        client = dashboard_app_module.web.test_client()
        response = client.get("/livez")

        assert response.status_code == 200
        assert response.get_data(as_text=True).strip() == "ok"
