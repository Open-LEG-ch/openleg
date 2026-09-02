# SPDX-License-Identifier: AGPL-3.0-or-later
"""Application factory contract."""

import subprocess
import sys
from pathlib import Path

import app


def test_import_is_inert_and_factory_instances_are_isolated():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os

os.environ.update({
    "DATABASE_URL": "postgresql://x:x@localhost/x",
    "ADMIN_TOKEN": "test123",
    "INTERNAL_TOKEN": "secret-internal",
    "APP_BASE_URL": "http://localhost:5003",
    "PUBLIC_SITE_URL": "https://openleg.ch",
    "ALLOWED_HOSTS": "localhost,127.0.0.1",
    "ADMIN_EMAIL": "admin@example.com",
    "SECRET_KEY": "app-factory-test-key",
    "SESSION_COOKIE_SECURE": "",
    "SESSION_COOKIE_SAMESITE": "Lax",
    "PERMANENT_SESSION_LIFETIME": "3600",
    "DASHBOARD_ACCESS_TOKEN_TTL_SECONDS": "",
    "DASHBOARD_EMAIL_TOKEN_TTL_SECONDS": "",
    "CRON_SECRET": "",
    "REDIS_URL": "",
})

from unittest.mock import patch

with (
    patch('database.is_db_available') as database_probe,
    patch('logging.basicConfig') as logging_probe,
):
    import app

database_probe.assert_not_called()
logging_probe.assert_not_called()
assert 'app' not in vars(app)
assert not hasattr(app, 'app')
try:
    from app import app as legacy_app
except ImportError:
    pass
else:
    raise AssertionError(f'legacy app export still available: {legacy_app!r}')
first = app.create_app(
    {'TESTING': True, 'SECRET_KEY': 'first', 'RATELIMIT_STORAGE_URI': 'memory://'},
    load_environment=False,
    check_database=False,
)
second = app.create_app(
    {'TESTING': True, 'SECRET_KEY': 'second', 'RATELIMIT_STORAGE_URI': 'memory://'},
    load_environment=False,
    check_database=False,
)
assert first is not second
assert first.config['SECRET_KEY'] == 'first'
assert second.config['SECRET_KEY'] == 'second'
assert first.test_client().get('/livez').status_code == 200
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_documented_gunicorn_target_uses_wsgi_entrypoint():
    security = Path("SECURITY.md").read_text()

    assert "gunicorn -w 4 -b 0.0.0.0:8000 wsgi:app" in security
    assert "app:app" not in security


def test_dev_port_follows_an_explicit_port_in_the_base_url():
    assert app._dev_port("http://localhost:5099") == 5099


def test_dev_port_falls_back_to_the_default_when_the_base_url_has_none():
    assert app._dev_port("https://openleg.ch") == 5003
    assert app._dev_port("https://openleg.ch", default=8080) == 8080
