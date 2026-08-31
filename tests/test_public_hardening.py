# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hardening contract: fail-closed cron auth, verified webhooks, complete env
template, gitignore coverage, no dead deploy surface."""

import importlib
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CRON_ENDPOINTS = (
    "/api/cron/process-emails",
    "/api/cron/refresh-public-data",
    "/api/cron/backfill-elcom",
    "/api/cron/process-billing",
    "/api/cron/verify-registry-entries",
)

# Every env var the code reads that a self-hoster may need to set.
REQUIRED_ENV_EXAMPLE_VARS = (
    "CRON_SECRET",
    "REDIS_URL",
    "DEEPSIGN_API_KEY",
    "DEEPSIGN_API_URL",
    "DEEPSIGN_WEBHOOK_SECRET",
    "DB_POOL_MIN",
    "DB_POOL_MAX",
)


def _load_app(env_overrides, remove=()):
    with patch.dict(
        os.environ,
        {
            "DATABASE_URL": "postgresql://x:x@localhost/x",
            "REDIS_URL": "memory://",
            "APP_BASE_URL": "http://localhost:5003",
            **env_overrides,
        },
    ):
        for key in remove:
            os.environ.pop(key, None)
        with (
            patch("database.is_db_available", return_value=True),
            patch("database._connection_pool", MagicMock()),
        ):
            import app as app_module

            return SimpleNamespace(
                app=app_module.create_app(load_environment=False), db=app_module.db
            )


@pytest.fixture
def app_without_cron_secret():
    module = _load_app({}, remove=("CRON_SECRET",))
    yield module


@pytest.fixture
def app_with_deepsign_secret():
    import deepsign_integration

    with patch.dict(os.environ, {"DEEPSIGN_WEBHOOK_SECRET": "test-webhook-secret"}):
        importlib.reload(deepsign_integration)
        module = _load_app({"DEEPSIGN_WEBHOOK_SECRET": "test-webhook-secret"})
        yield module
    importlib.reload(deepsign_integration)


def test_cron_endpoints_fail_closed_without_secret(app_without_cron_secret):
    client = app_without_cron_secret.app.test_client()
    for endpoint in CRON_ENDPOINTS:
        response = client.post(endpoint)
        assert response.status_code == 403, (
            f"{endpoint} must fail closed when CRON_SECRET is unset, "
            f"got {response.status_code}"
        )


def test_deepsign_webhook_rejects_unsigned_when_secret_set(app_with_deepsign_secret):
    client = app_with_deepsign_secret.app.test_client()
    response = client.post(
        "/webhook/deepsign",
        json={"event": "document.signed", "document_id": "doc-1"},
    )
    assert response.status_code == 403


def test_deepsign_webhook_accepts_valid_signature(app_with_deepsign_secret):
    import json as _json

    import deepsign_integration

    body = _json.dumps({"event": "document.signed", "document_id": "doc-1"}).encode()
    signature = deepsign_integration.sign_webhook_payload(body)
    with patch.object(
        deepsign_integration,
        "handle_webhook",
        return_value={"action": "signature_completed", "document_id": "doc-1"},
    ) as handler:
        response = app_with_deepsign_secret.app.test_client().post(
            "/webhook/deepsign",
            data=body,
            content_type="application/json",
            headers={"X-DeepSign-Signature": signature},
        )
    assert response.status_code == 200
    handler.assert_called_once()


def test_deepsign_signature_verification_helper():
    import deepsign_integration

    with patch.object(deepsign_integration, "WEBHOOK_SECRET", "s3cret"):
        body = b'{"event":"document.signed"}'
        good = deepsign_integration.sign_webhook_payload(body)
        assert deepsign_integration.verify_webhook_signature(body, good)
        assert not deepsign_integration.verify_webhook_signature(body, "bad")
        assert not deepsign_integration.verify_webhook_signature(body, "")

    with patch.object(deepsign_integration, "WEBHOOK_SECRET", ""):
        # Without a configured secret the check is a no-op (dev mode).
        assert deepsign_integration.verify_webhook_signature(b"x", "")


def test_env_example_documents_all_required_vars():
    content = Path(PROJECT_ROOT, ".env.example").read_text(encoding="utf-8")
    missing = [var for var in REQUIRED_ENV_EXAMPLE_VARS if var not in content]
    assert missing == []


def test_gitignore_covers_local_artifacts():
    should_ignore = (
        ".env.staging",
        ".env.backup",
        "backups/dump.sql",
        "snapshot.sql.gz",
        "db.dump",
        "output/anything.png",
        "gitleaks.sarif",
    )
    unignored = []
    for path in should_ignore:
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "-q", path],
            cwd=PROJECT_ROOT,
            check=False,
        )
        if result.returncode != 0:
            unignored.append(path)
    assert unignored == []

    # Public configuration examples must remain trackable.
    for path in (".env.example",):
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "-q", path],
            cwd=PROJECT_ROOT,
            check=False,
        )
        assert result.returncode != 0, f"{path} must not be gitignored"

    # Local agent contracts must never be tracked in the public repository.
    for path in ("CLAUDE.md", "AGENTS.md"):
        result = subprocess.run(
            ["git", "check-ignore", "-q", path], cwd=PROJECT_ROOT, check=False
        )
        assert result.returncode == 0, f"{path} must be gitignored"


def test_dead_deploy_configs_are_untracked():
    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    for path in ("railway.toml", "Procfile", "passenger_wsgi.py"):
        assert path not in tracked
