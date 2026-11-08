# SPDX-License-Identifier: AGPL-3.0-or-later
"""Production processes require an explicit durable session signing key."""

import os
import subprocess
import sys
from pathlib import Path

import psycopg2.extensions
import pytest

import app_config
from tests import test_interest_postgres
from tests.native_environment import isolated_environment
from tests.test_i18n import _TEST_ENVIRONMENT

interest_database = test_interest_postgres.interest_database


@pytest.mark.parametrize(
    "key",
    [
        None,
        "",
        "   ",
        "short",
        "your-secret-key-here",
        "generate-a-strong-random-key-here",
    ],
)
@pytest.mark.parametrize(
    "environment", [{"FLASK_ENV": "production"}, {"APP_BASE_URL": "https://openleg.ch"}]
)
def test_production_refuses_missing_weak_or_example_session_keys(key, environment):
    env = dict(environment)
    if key is not None:
        env["SECRET_KEY"] = key
    with pytest.raises(ValueError, match="SECRET_KEY"):
        app_config.build_config(env)


def test_production_keeps_a_configured_durable_key():
    key = "synthetic-key-" + "x" * 32
    assert (
        app_config.build_config({"FLASK_ENV": "production", "SECRET_KEY": key})[
            "SECRET_KEY"
        ]
        == key
    )


def test_explicit_testing_can_run_without_a_production_key():
    assert app_config.build_config(
        {"APP_BASE_URL": "https://localhost"}, {"TESTING": True}
    )["SECRET_KEY"]


@pytest.mark.integration
@pytest.mark.parametrize("key", [None, "generate-a-strong-random-key-here"])
def test_wsgi_startup_refuses_an_unconfigured_production_key(interest_database, key):
    with interest_database.get_connection() as connection:
        assert connection.get_dsn_parameters()["dbname"].startswith("openleg_interest_")
        database_url = psycopg2.extensions.make_dsn(
            os.environ["DATABASE_URL"],
            dbname=connection.get_dsn_parameters()["dbname"],
        )
    environment = isolated_environment(
        _TEST_ENVIRONMENT
        | {
            "FLASK_ENV": "production",
            "APP_BASE_URL": "https://synthetic.invalid",
            "DATABASE_URL": database_url,
            "REDIS_URL": "memory://",
            "PYTHON_DOTENV_DISABLED": "1",
        }
    )
    environment.pop("SECRET_KEY", None)
    if key is not None:
        environment["SECRET_KEY"] = key
    result = subprocess.run(
        [sys.executable, "-c", "import wsgi"],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode != 0
    assert "Production requires an explicit SECRET_KEY" in result.stderr
