# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run the locale tests in fresh processes with deliberately hostile settings."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "outside",
    [
        {"PERMANENT_SESSION_LIFETIME": "sentinel"},
        {"PERMANENT_SESSION_LIFETIME": "7200"},
        {
            "BABEL_DEFAULT_LOCALE": "unsupported-host-locale",
            "APP_BASE_URL": "https://outside.invalid",
            "PUBLIC_SITE_URL": "not-an-origin",
            "ALLOWED_HOSTS": "outside.invalid",
            "MATOMO_SITE_ID": "not-a-site-id",
            "GA4_MEASUREMENT_ID": "outside-analytics",
        },
        {
            "SECRET_KEY": "synthetic-outside-secret",
            "ADMIN_TOKEN": "synthetic-outside-admin",
            "INTERNAL_TOKEN": "synthetic-outside-internal",
            "CRON_SECRET": "synthetic-outside-cron",
            "SESSION_COOKIE_SECURE": "true",
            "SESSION_COOKIE_SAMESITE": "Strict",
            "DASHBOARD_ACCESS_TOKEN_TTL_SECONDS": "61",
            "DASHBOARD_EMAIL_TOKEN_TTL_SECONDS": "62",
        },
        {
            "PERMANENT_SESSION_LIFETIME": "sentinel",
            "DB_POOL_MIN": "sentinel",
            "DB_POOL_MAX": "sentinel",
            "SMTP_PORT": "sentinel",
            "SMTP_USER": "synthetic-outside-user",
            "SMTP_PASSWORD": "synthetic-outside-password",
            "BABEL_DEFAULT_LOCALE": "it_CH",
            "PUBLIC_SITE_URL": "invalid",
            "DATABASE_URL": "postgresql://127.0.0.1:1/synthetic_unreachable",
            "REDIS_URL": "redis://127.0.0.1:1/0",
            "UNRECOGNIZED_HOST_SETTING": "must-not-leak",
        },
    ],
    ids=[
        "invalid-lifetime",
        "different-lifetime",
        "locale-and-urls",
        "auth-and-session",
        "hostile-import",
    ],
)
def test_locale_suite_is_independent_of_host_environment(tmp_path, outside):
    # Preserve interpreter/coverage/native-library tooling; the locale fixture
    # must isolate application settings itself, before its first app import.
    environment = {**os.environ, **outside, "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *sorted(
                str(path.relative_to(Path(__file__).resolve().parents[1]))
                for path in Path(__file__).parent.glob("test_i18n*.py")
                if path.name != Path(__file__).name
            ),
            "-q",
            "-o",
            "addopts=",
            "-o",
            f"cache_dir={tmp_path / 'cache'}",
            f"--basetemp={tmp_path / 'temp'}",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env={**environment, "PYTEST_ADDOPTS": ""},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "skipped" not in output.lower(), output
