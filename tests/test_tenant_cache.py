"""TDD tests for tenant cache backed by Redis via cache.py."""
import pytest
from unittest.mock import patch, MagicMock
import json


@pytest.fixture
def mock_redis():
    mock = MagicMock()
    mock.get.return_value = None
    mock.setex.return_value = True
    mock.delete.return_value = True
    with patch('cache._get_redis', return_value=mock):
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
        mock_redis.get.assert_called_with("openleg:tenant:baden")

    def test_cache_miss_falls_back_to_db(self, mock_redis):
        mock_redis.get.return_value = None
        mock_db = MagicMock()
        conn_mock = MagicMock()
        cur_mock = MagicMock()
        cur_mock.fetchone.return_value = {
            "territory": "baden", "utility_name": "AEW",
            "primary_color": "#c7021a", "secondary_color": "#f59e0b",
            "contact_email": "", "contact_phone": "", "legal_entity": "",
            "dso_contact": "", "active": True, "config": {}
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
        # Should have written to Redis
        mock_redis.setex.assert_called_once()

    def test_cache_miss_no_db_returns_default(self, mock_redis):
        from tenant import get_tenant_config
        result = get_tenant_config("zurich", db=None)
        assert result["territory"] == "zurich"
        assert result["city_name"] == "Zürich"

    def test_redis_down_still_works(self):
        """If Redis is completely down, tenant resolution still works."""
        with patch('cache._get_redis', side_effect=Exception("Connection refused")):
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
