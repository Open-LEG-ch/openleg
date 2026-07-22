# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for app-level organic growth routes."""

import importlib
import os
from unittest.mock import MagicMock, patch

import pytest

import public_data
from ranking import Ranking


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
    with patch.dict(
        os.environ,
        {
            "DATABASE_URL": "postgresql://x:x@localhost/x",
            "REDIS_URL": "memory://",
            "CRON_SECRET": "test-cron-secret",
            "APP_BASE_URL": "http://localhost:5003",
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


def test_security_policy_allows_google_analytics_region_collect(full_app_module):
    client = full_app_module.app.test_client()
    resp = client.get("/robots.txt")

    csp = resp.headers.get("Content-Security-Policy", "")
    assert _csp_sources(csp, "connect-src") == {
        "'self'",
        "https://www.google-analytics.com",
        "https://region1.google-analytics.com",
        "https://www.googletagmanager.com",
    }


def test_security_policy_allows_brand_font_assets(full_app_module):
    client = full_app_module.app.test_client()
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
    client = full_app_module.app.test_client()

    resp = client.get("/favicon.ico")

    assert resp.status_code == 200
    assert resp.mimetype == "image/vnd.microsoft.icon"


def test_shared_tailwind_partial_uses_local_css():
    with open("templates/partials/tailwind_brand.html") as f:
        content = f.read()

    assert "cdn.tailwindcss.com" not in content
    assert "/static/css/openleg.css" in content


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
    assert "Öffentliches App-Repo" in html
    assert "Privates Ops-Repo" in html
    assert "git clone https://github.com/Open-LEG-ch/openleg.git" in html
    assert "github.com/Open-LEG-ch/openleg" in html
    assert 'type="application/ld+json"' in html
    assert '"@type": "SoftwareApplication"' in html
    assert '"applicationCategory": "EnergyApplication"' in html


@pytest.mark.parametrize(
    ("route", "headline"),
    [
        ("/how-it-works", "So funktioniert"),
        ("/leg-gruenden", "LEG gründen"),
        ("/leg-kalkulator", "LEG-Kalkulator"),
        ("/pricing", "Kostenlos"),
    ],
)
def test_public_guides_have_share_metadata(full_app_module, route, headline):
    client = full_app_module.app.test_client()

    resp = client.get(route)

    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="ignore")
    assert headline in html
    assert '<meta name="description"' in html
    assert f'rel="canonical" href="http://localhost:5003{route}"' in html
    assert 'property="og:title"' in html
    assert 'property="og:description"' in html
    assert f'property="og:url" content="http://localhost:5003{route}"' in html
    assert 'name="twitter:card" content="summary_large_image"' in html
    assert '"@type": "BreadcrumbList"' in html


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


# === Issue #210: homepage ranking preview routed through the Ranking facade ===

_RANKING_EXTREMES_PROFILES = [
    {"bfs_number": 1, "name": "Overcap", "kanton": "ZH", "pv_score_pct": 140},
    {"bfs_number": 2, "name": "G2", "kanton": "ZH", "pv_score_pct": 95},
    {"bfs_number": 3, "name": "G3", "kanton": "ZH", "pv_score_pct": 90},
    {"bfs_number": 4, "name": "G4", "kanton": "ZH", "pv_score_pct": 85},
    {"bfs_number": 5, "name": "G5", "kanton": "ZH", "pv_score_pct": 80},
    {"bfs_number": 6, "name": "G6", "kanton": "ZH", "pv_score_pct": 75},
    {"bfs_number": 7, "name": "G7", "kanton": "ZH", "pv_score_pct": 70},
    {"bfs_number": 8, "name": "Unscored", "kanton": "ZH", "pv_score_pct": None},
]


def test_ranking_extremes_uses_facade(full_app_module, monkeypatch):
    mock_load = MagicMock(return_value=Ranking(_RANKING_EXTREMES_PROFILES))
    monkeypatch.setattr(full_app_module.Ranking, "load", mock_load)

    best, worst, total = full_app_module._ranking_extremes(n=3)

    mock_load.assert_called_once_with()
    assert total == 7  # bfs 8 has no score and is excluded
    assert best == [
        {"rank": 1, "name": "Overcap", "kanton": "ZH", "bfs_number": 1, "score": 100.0},
        {"rank": 2, "name": "G2", "kanton": "ZH", "bfs_number": 2, "score": 95.0},
        {"rank": 3, "name": "G3", "kanton": "ZH", "bfs_number": 3, "score": 90.0},
    ]
    assert worst == [
        {"rank": 7, "name": "G7", "kanton": "ZH", "bfs_number": 7, "score": 70.0},
        {"rank": 6, "name": "G6", "kanton": "ZH", "bfs_number": 6, "score": 75.0},
        {"rank": 5, "name": "G5", "kanton": "ZH", "bfs_number": 5, "score": 80.0},
    ]


def test_ranking_extremes_empty_when_too_few_scored(full_app_module, monkeypatch):
    profiles = _RANKING_EXTREMES_PROFILES[:2]
    mock_load = MagicMock(return_value=Ranking(profiles))
    monkeypatch.setattr(full_app_module.Ranking, "load", mock_load)

    best, worst, total = full_app_module._ranking_extremes(n=3)

    assert (best, worst, total) == ([], [], 2)


def test_ranking_extremes_falls_back_on_load_failure(full_app_module, monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(full_app_module.Ranking, "load", _boom)

    best, worst, total = full_app_module._ranking_extremes(n=3)

    assert (best, worst, total) == ([], [], 0)
