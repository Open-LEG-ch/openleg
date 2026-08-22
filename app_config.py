# SPDX-License-Identifier: AGPL-3.0-or-later
"""The application configuration, as a value.

Reading the environment, bounding a TTL and validating `PUBLIC_SITE_URL` have
nothing to do with Flask, and building an application to reach them made every
test of a hostname rule pay for one. `build_config` takes the environment and
any explicit overrides and returns the mapping `create_app` hands to Flask.

`PUBLIC_SITE_URL` is the security boundary here: templates interpolate it into
links, so `validated_public_site_url` reduces it to a bare origin and refuses
anything carrying credentials, a path, a query or a fragment.
"""

import ipaddress
import logging
import os
import re
from datetime import timedelta
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

DEFAULT_APP_BASE_URL = "http://localhost:5003"
DEFAULT_PUBLIC_SITE_URL = "https://openleg.ch"
DEFAULT_ADMIN_EMAIL = "hallo@openleg.ch"
DEFAULT_ALLOWED_HOSTS = "localhost,127.0.0.1"
DEFAULT_REDIS_URL = "redis://redis:6379/1"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MIN_TOKEN_TTL_SECONDS = 60
MAX_TOKEN_TTL_SECONDS = 86_400
TRUTHY = {"true", "1", "yes", "on"}

_DNS_LABEL_RE = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")


def parse_ttl_seconds(raw, default):
    """Parse a token TTL from the environment, bounded to a minute and a day."""
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid dashboard TTL value %r, using default %s", raw, default)
        return default
    return max(MIN_TOKEN_TTL_SECONDS, min(value, MAX_TOKEN_TTL_SECONDS))


def _contains_invalid_characters(value):
    return any(char.isspace() or ord(char) < 32 for char in value)


def _is_valid_hostname(hostname):
    if not hostname or "%" in hostname:
        return False
    if _contains_invalid_characters(hostname):
        return False
    if "[" in hostname or "]" in hostname:
        return False
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        pass
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError:
        return False
    ascii_hostname = ascii_hostname.removesuffix(".")
    if len(ascii_hostname) > 253:
        return False
    return all(_DNS_LABEL_RE.match(label) for label in ascii_hostname.split("."))


def _canonical_origin(parsed):
    hostname = parsed.hostname
    host = f"[{hostname}]" if ":" in hostname else hostname
    if parsed.port is not None:
        return f"{parsed.scheme}://{host}:{parsed.port}"
    return f"{parsed.scheme}://{host}"


def validated_public_site_url(value):
    """Reduce PUBLIC_SITE_URL to a bare origin, or refuse it."""
    if _contains_invalid_characters(value):
        raise ValueError("PUBLIC_SITE_URL must be an absolute HTTP(S) URL")
    if "?" in value or "#" in value or ";" in value:
        raise ValueError("PUBLIC_SITE_URL must not contain credentials or suffixes")
    try:
        parsed = urlsplit(value)
    except ValueError as error:
        raise ValueError("PUBLIC_SITE_URL must be an absolute HTTP(S) URL") from error
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or not parsed.hostname
    ):
        raise ValueError("PUBLIC_SITE_URL must be an absolute HTTP(S) URL")
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("PUBLIC_SITE_URL must not contain credentials or suffixes")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("PUBLIC_SITE_URL must have a valid port") from error
    if parsed.netloc.endswith(":") or (port is not None and port < 1):
        raise ValueError("PUBLIC_SITE_URL must have a valid port")
    if not _is_valid_hostname(parsed.hostname):
        raise ValueError("PUBLIC_SITE_URL must have a valid hostname")
    return _canonical_origin(parsed)


def build_config(env=None, overrides=None):
    """The mapping create_app hands to Flask, from the environment and overrides.

    Cookie security has three sources, in order: an explicit override wins, then
    SESSION_COOKIE_SECURE from the environment, then an https APP_BASE_URL.
    """
    env = os.environ if env is None else env
    app_base_url = env.get("APP_BASE_URL", DEFAULT_APP_BASE_URL)
    secure_cookie_env = env.get("SESSION_COOKIE_SECURE")
    session_cookie_secure = app_base_url.startswith("https://")
    if secure_cookie_env is not None:
        session_cookie_secure = secure_cookie_env.strip().lower() in TRUTHY

    config = {
        "JSON_SORT_KEYS": False,
        "SECRET_KEY": env.get("SECRET_KEY") or os.urandom(32).hex(),
        "SESSION_COOKIE_SECURE": session_cookie_secure,
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": env.get("SESSION_COOKIE_SAMESITE", "Lax"),
        "PERMANENT_SESSION_LIFETIME": timedelta(
            seconds=int(env.get("PERMANENT_SESSION_LIFETIME", "3600"))
        ),
        "DASHBOARD_ACCESS_TOKEN_TTL_SECONDS": parse_ttl_seconds(
            env.get("DASHBOARD_ACCESS_TOKEN_TTL_SECONDS"), 900
        ),
        "DASHBOARD_EMAIL_TOKEN_TTL_SECONDS": parse_ttl_seconds(
            env.get("DASHBOARD_EMAIL_TOKEN_TTL_SECONDS"), 86_400
        ),
        "MAX_CONTENT_LENGTH": MAX_UPLOAD_BYTES,
        "APP_BASE_URL": app_base_url,
        "SITE_URL": app_base_url.rstrip("/"),
        "PUBLIC_SITE_URL": env.get("PUBLIC_SITE_URL", DEFAULT_PUBLIC_SITE_URL),
        "ALLOWED_HOSTS": env.get("ALLOWED_HOSTS", DEFAULT_ALLOWED_HOSTS).split(","),
        "ADMIN_EMAIL": env.get("ADMIN_EMAIL", DEFAULT_ADMIN_EMAIL),
        "CRON_SECRET": env.get("CRON_SECRET", "").strip(),
        "RATELIMIT_STORAGE_URI": env.get("REDIS_URL", DEFAULT_REDIS_URL),
    }

    if overrides:
        config.update(overrides)
        if "APP_BASE_URL" in overrides and "SITE_URL" not in overrides:
            config["SITE_URL"] = overrides["APP_BASE_URL"].rstrip("/")
        if (
            "SESSION_COOKIE_SECURE" not in overrides
            and secure_cookie_env is None
            and config["APP_BASE_URL"].startswith("https://")
        ):
            config["SESSION_COOKIE_SECURE"] = True

    return config
