"""Redis-backed caching layer for OpenLEG.

Falls back gracefully if Redis is unavailable (returns None, no-ops on writes).
All keys prefixed with "openleg:" to avoid collisions.
"""
import os
import json
import logging

logger = logging.getLogger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
KEY_PREFIX = "openleg:"
DEFAULT_TTL = 3600  # 1 hour

_redis_client = None


def _get_redis():
    global _redis_client
    if _redis_client is None:
        import redis
        _redis_client = redis.from_url(REDIS_URL, decode_responses=False)
    return _redis_client


def cache_get(key):
    """Get value from cache. Returns None on miss or error."""
    try:
        raw = _get_redis().get(f"{KEY_PREFIX}{key}")
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as e:
        logger.debug(f"Cache get error for {key}: {e}")
        return None


def cache_set(key, value, ttl=DEFAULT_TTL):
    """Set value in cache with TTL (seconds). No-op on error."""
    try:
        _get_redis().setex(f"{KEY_PREFIX}{key}", ttl, json.dumps(value))
    except Exception as e:
        logger.debug(f"Cache set error for {key}: {e}")


def cache_delete(key):
    """Delete key from cache. No-op on error."""
    try:
        _get_redis().delete(f"{KEY_PREFIX}{key}")
    except Exception as e:
        logger.debug(f"Cache delete error for {key}: {e}")


def cache_clear_prefix(prefix):
    """Delete all keys matching prefix. Use for tenant invalidation."""
    try:
        r = _get_redis()
        pattern = f"{KEY_PREFIX}{prefix}*"
        keys = r.keys(pattern)
        if keys:
            r.delete(*keys)
    except Exception as e:
        logger.debug(f"Cache clear error for {prefix}: {e}")
