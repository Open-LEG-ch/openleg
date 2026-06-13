# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for app-level organic growth routes."""

import importlib
import os
from unittest.mock import MagicMock, patch

import pytest

import public_data


def _disable_rate_limit_hooks(flask_app):
    hooks = list(flask_app.before_request_funcs.get(None, []))
    flask_app.before_request_funcs[None] = [
        hook
        for hook in hooks
        if not (
            getattr(hook, "__module__", "").startswith("flask_limiter")
            or getattr(hook, "__name__", "") == "_check_request_limit"
        )
    ]
    return hooks


@pytest.fixture
def full_app_module():
    with patch.dict(
        os.environ,
        {
            "DATABASE_URL": "postgresql://x:x@localhost/x",
            "REDIS_URL": "memory://",
            "CRON_SECRET": "test-cron-secret",
        },
    ):
        with (
            patch("database.is_db_available", return_value=True),
            patch("database._connection_pool", MagicMock()),
        ):
            import app as app_module

            app_module = importlib.reload(app_module)
            hooks = _disable_rate_limit_hooks(app_module.app)
            try:
                yield app_module
            finally:
                app_module.app.before_request_funcs[None] = hooks


def test_robots_allows_api_docs_but_blocks_api(full_app_module):
    client = full_app_module.app.test_client()
    resp = client.get("/robots.txt")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8", errors="ignore")
    assert "Allow: /api/v1/docs" in body
    assert "Disallow: /api/" in body


def test_sitemap_contains_directory_docs_and_profile_urls(full_app_module, monkeypatch):
    monkeypatch.setattr(
        full_app_module.db,
        "get_all_municipality_profile_bfs_numbers",
        lambda: [261, 247],
    )
    client = full_app_module.app.test_client()

    resp = client.get("/sitemap.xml")
    assert resp.status_code == 200
    xml = resp.data.decode("utf-8", errors="ignore")
    assert "/gemeinde/verzeichnis" in xml
    assert "/api/v1/docs" in xml
    assert "/rangliste/fortschritte" in xml
    assert "/rangliste/vergleich" in xml
    assert "/gemeinde/profil/261" in xml
    assert "/gemeinde/profil/247" in xml
    assert "/admin/" not in xml
    assert "/api/v1/municipalities" not in xml


def test_open_source_page_explains_codebase(full_app_module):
    client = full_app_module.app.test_client()

    resp = client.get("/open-source")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="ignore")
    assert "Open Source" in html
    assert "Flask" in html
    assert "PostgreSQL" in html
    assert "Redis" in html
    assert "Caddy" in html
    assert "Datenpipeline" in html
    assert "Public App Repo" in html
    assert "Private Ops Repo" in html
    assert "git clone https://github.com/Open-LEG-ch/openleg.git" in html
    assert "github.com/Open-LEG-ch/openleg" in html


def test_backfill_elcom_invalid_secret_returns_403_and_no_mutation(
    full_app_module, monkeypatch
):
    called = {"fetch": 0, "save": 0, "list": 0}
    monkeypatch.setattr(
        full_app_module.db,
        "get_profile_bfs_missing_elcom_tariffs",
        lambda year, limit: called.__setitem__("list", called["list"] + 1),
    )
    monkeypatch.setattr(
        public_data,
        "fetch_elcom_tariffs",
        lambda bfs, year=2026: called.__setitem__("fetch", called["fetch"] + 1),
    )
    monkeypatch.setattr(
        full_app_module.db,
        "save_elcom_tariffs",
        lambda rows: called.__setitem__("save", called["save"] + 1),
    )
    client = full_app_module.app.test_client()

    resp = client.post("/api/cron/backfill-elcom")
    assert resp.status_code == 403
    assert called["list"] == 0
    assert called["fetch"] == 0
    assert called["save"] == 0


def test_backfill_elcom_processes_batch_and_returns_summary(
    full_app_module, monkeypatch
):
    monkeypatch.setattr(
        full_app_module.db,
        "get_profile_bfs_missing_elcom_tariffs",
        lambda year, limit: [261, 247],
    )
    monkeypatch.setattr(
        public_data,
        "fetch_elcom_tariffs",
        lambda bfs, year=2026: [
            {
                "bfs_number": bfs,
                "year": year,
                "operator_name": "EKZ",
                "category": "H4",
            }
        ],
    )
    monkeypatch.setattr(
        full_app_module.db, "save_elcom_tariffs", lambda rows: len(rows)
    )
    client = full_app_module.app.test_client()

    resp = client.post(
        "/api/cron/backfill-elcom?limit=2&year=2026",
        headers={"X-Cron-Secret": "test-cron-secret"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["processed"] == 2
    assert data["saved"] == 2
    assert data["errors"] == []
