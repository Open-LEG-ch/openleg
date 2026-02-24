"""TDD tests for cache.py - Redis-backed caching layer."""
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def mock_redis():
    mock = MagicMock()
    mock.get.return_value = None
    mock.setex.return_value = True
    mock.delete.return_value = True
    with patch('cache._get_redis', return_value=mock):
        yield mock


class TestCacheOperations:
    """Test cache get/set/delete."""

    def test_cache_miss(self, mock_redis):
        from cache import cache_get
        assert cache_get("nonexistent") is None

    def test_cache_hit(self, mock_redis):
        from cache import cache_get
        import json
        mock_redis.get.return_value = json.dumps({"data": 42}).encode()
        result = cache_get("mykey")
        assert result == {"data": 42}

    def test_cache_set(self, mock_redis):
        from cache import cache_set
        cache_set("mykey", {"data": 42}, ttl=300)
        mock_redis.setex.assert_called_once()

    def test_cache_delete(self, mock_redis):
        from cache import cache_delete
        cache_delete("mykey")
        mock_redis.delete.assert_called_once_with("openleg:mykey")


class TestTenantCache:
    """Test tenant-specific caching."""

    def test_tenant_cache_key(self, mock_redis):
        from cache import cache_get
        cache_get("tenant:baden")
        mock_redis.get.assert_called_with("openleg:tenant:baden")

    def test_cache_fallback_on_error(self):
        """If Redis is down, cache operations return None / no-op."""
        with patch('cache._get_redis', side_effect=Exception("Connection refused")):
            from cache import cache_get, cache_set
            assert cache_get("key") is None
            cache_set("key", "val")  # should not raise
