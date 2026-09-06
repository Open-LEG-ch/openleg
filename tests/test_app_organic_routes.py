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


def _csp_sources(header, directive_name):
    for directive in header.split(";"):
        parts = directive.strip().split()
        if parts and parts[0] == directive_name:
            return set(parts[1:])
    return set()


@pytest.fixture
def full_app_module():
    with (
        patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql://x:x@localhost/x",
                "REDIS_URL": "memory://",
                "CRON_SECRET": "test-cron-secret",
                "APP_BASE_URL": "http://localhost:5003",
            },
        ),
        patch("database.is_db_available", return_value=True),
        patch("database._connection_pool", MagicMock()),
    ):
        import app as app_module

        app_module = importlib.reload(app_module)
        app_module.web = app_module.create_app(load_environment=False)
        hooks = _disable_rate_limit_hooks(app_module.web)
        try:
            yield app_module
        finally:
            app_module.web.before_request_funcs[None] = hooks


def test_security_policy_allows_google_analytics_region_collect(full_app_module):
    client = full_app_module.web.test_client()
    resp = client.get("/dashboard/demo")

    csp = resp.headers.get("Content-Security-Policy", "")
    assert _csp_sources(csp, "connect-src") == {
        "'self'",
        "https://www.google-analytics.com",
        "https://region1.google-analytics.com",
        "https://www.googletagmanager.com",
    }


def test_security_policy_allows_brand_font_assets(full_app_module):
    client = full_app_module.web.test_client()
    resp = client.get("/dashboard/demo")

    csp = resp.headers.get("Content-Security-Policy", "")
    assert _csp_sources(csp, "style-src") == {
        "'self'",
        "'unsafe-inline'",
        "https://unpkg.com",
        "https://cdn.jsdelivr.net",
        "https://fonts.googleapis.com",
    }
    assert _csp_sources(csp, "font-src") == {
        "'self'",
        "data:",
        "https://fonts.gstatic.com",
    }


def test_root_favicon_serves_static_icon(full_app_module):
    client = full_app_module.web.test_client()

    resp = client.get("/favicon.ico")

    assert resp.status_code == 200
    assert resp.mimetype == "image/vnd.microsoft.icon"


def test_llms_txt_summarizes_facts_and_pages(full_app_module):
    client = full_app_module.web.test_client()

    resp = client.get("/llms.txt")

    assert resp.status_code == 200
    assert resp.mimetype == "text/plain"
    body = resp.get_data(as_text=True)
    assert body.startswith("# OpenLEG\n")
    assert "40%" in body
    assert "Art. 19h StromVV" in body
    assert f"{full_app_module.web.config['SITE_URL']}/how-it-works" in body


def test_robots_txt_welcomes_llm_crawlers_and_points_at_llms_txt(full_app_module):
    client = full_app_module.web.test_client()

    resp = client.get("/robots.txt")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    for agent in (
        "GPTBot",
        "OAI-SearchBot",
        "ChatGPT-User",
        "ClaudeBot",
        "PerplexityBot",
    ):
        assert f"User-agent: {agent}\nAllow: /" in body
    assert body.count("\n\n") >= 7
    assert "Sitemap: " in body
    assert "/llms.txt" in body


def test_shared_tailwind_partial_uses_local_css():
    with open("templates/partials/tailwind_brand.html") as f:
        content = f.read()

    assert "cdn.tailwindcss.com" not in content
    assert "/static/css/openleg.css" in content


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
    client = full_app_module.web.test_client()

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
    client = full_app_module.web.test_client()

    resp = client.post(
        "/api/cron/backfill-elcom?limit=2&year=2026",
        headers={"X-Cron-Secret": "test-cron-secret"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["processed"] == 2
    assert data["saved"] == 2
    assert data["errors"] == []
