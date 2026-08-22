# SPDX-License-Identifier: AGPL-3.0-or-later
"""The scheduled-job surface lives apart from the product routes.

app.py mixed four jobs: product routes, the neighbour helpers, the cron
endpoints, and the application factory. No product request ever reaches a cron
route, and the operator surface was split off the same way in #271.
"""

import ast
from pathlib import Path

import pytest

import app as app_module

ROOT = Path(__file__).resolve().parents[1]

CRON_RULES = {
    "/api/cron/process-emails",
    "/api/cron/refresh-public-data",
    "/api/cron/backfill-elcom",
    "/api/cron/process-billing",
    "/api/cron/verify-registry-entries",
    "/api/email/stats",
}

SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "archive",
    "node_modules",
    "private",
    "scripts",
    "tests",
}


def _product_modules():
    for path in ROOT.rglob("*.py"):
        if SKIP_DIRS.intersection(path.relative_to(ROOT).parts):
            continue
        yield path


@pytest.fixture
def application():
    return app_module.create_app(
        {
            "TESTING": True,
            "RATELIMIT_STORAGE_URI": "memory://",
            "APP_BASE_URL": "http://localhost",
        },
        load_environment=False,
        check_database=False,
    )


def test_every_cron_route_still_answers_at_the_same_url(application):
    registered = {str(rule) for rule in application.url_map.iter_rules()}

    assert CRON_RULES <= registered


def test_every_cron_route_is_served_by_the_cron_blueprint(application):
    owners = {
        str(rule): rule.endpoint.split(".")[0]
        for rule in application.url_map.iter_rules()
        if str(rule) in CRON_RULES
    }

    assert owners == dict.fromkeys(CRON_RULES, "cron")


def test_the_cron_secret_guard_has_one_home():
    """Two copies of a fail-closed guard is one copy that stops being updated."""
    homes = []
    for path in _product_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == "_require_cron_secret"
            ):
                homes.append(path.name)

    assert homes == ["cron.py"]


def test_app_no_longer_defines_cron_routes():
    source = (ROOT / "app.py").read_text(encoding="utf-8")

    assert "/api/cron/" not in source
    assert "_require_cron_secret" not in source
