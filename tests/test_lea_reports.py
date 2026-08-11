# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for LEA report webhook receiver and admin view."""

import os
from unittest.mock import MagicMock, patch

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestLeaReportWebhook:
    def test_lea_report_rejects_without_token(self):
        with (
            patch.dict(
                os.environ,
                {
                    "DATABASE_URL": "postgresql://x:x@localhost/x",
                    "ADMIN_TOKEN": "test123",
                    "INTERNAL_TOKEN": "secret-internal",
                },
            ),
            patch("database.init_db", return_value=True),
            patch("database._connection_pool", MagicMock()),
            patch("database.is_db_available", return_value=True),
        ):
            from app import create_app

            client = create_app(
                {"RATELIMIT_STORAGE_URI": "memory://"}, load_environment=False
            ).test_client()
            resp = client.post(
                "/api/internal/lea-report",
                json={"job_name": "test", "summary": "hi"},
            )
            assert resp.status_code == 403

    def test_lea_report_accepts_with_valid_token(self):
        with (
            patch.dict(
                os.environ,
                {
                    "DATABASE_URL": "postgresql://x:x@localhost/x",
                    "ADMIN_TOKEN": "test123",
                    "INTERNAL_TOKEN": "secret-internal",
                },
            ),
            patch("database.init_db", return_value=True),
            patch("database._connection_pool", MagicMock()),
            patch("database.is_db_available", return_value=True),
            patch("database.save_lea_report", return_value=True),
        ):
            from app import create_app

            client = create_app(
                {"RATELIMIT_STORAGE_URI": "memory://"}, load_environment=False
            ).test_client()
            resp = client.post(
                "/api/internal/lea-report",
                json={"job_name": "daily-health-check", "summary": "All good"},
                headers={"X-Internal-Token": "secret-internal"},
            )
            assert resp.status_code == 200


class TestAdminLeaReports:
    def test_admin_lea_reports_requires_admin(self):
        with (
            patch.dict(
                os.environ,
                {
                    "DATABASE_URL": "postgresql://x:x@localhost/x",
                    "ADMIN_TOKEN": "test123",
                },
            ),
            patch("database.init_db", return_value=True),
            patch("database._connection_pool", MagicMock()),
            patch("database.is_db_available", return_value=True),
        ):
            from app import create_app

            client = create_app(
                {"RATELIMIT_STORAGE_URI": "memory://"}, load_environment=False
            ).test_client()
            resp = client.get("/admin/lea-reports")
            assert resp.status_code == 403

    def test_admin_lea_reports_returns_json(self):
        with (
            patch.dict(
                os.environ,
                {
                    "DATABASE_URL": "postgresql://x:x@localhost/x",
                    "ADMIN_TOKEN": "test123",
                },
            ),
            patch("database.init_db", return_value=True),
            patch("database._connection_pool", MagicMock()),
            patch("database.is_db_available", return_value=True),
            patch("database.get_lea_reports", return_value=[]),
        ):
            from app import create_app

            client = create_app(
                {"RATELIMIT_STORAGE_URI": "memory://"}, load_environment=False
            ).test_client()
            resp = client.get(
                "/admin/lea-reports", headers={"X-Admin-Token": "test123"}
            )
            assert resp.status_code == 200
            assert "reports" in resp.get_json()


class TestLeaReportRouteExists:
    """Static test: verify routes exist in admin.py source."""

    def test_lea_report_post_route_in_source(self):
        with open(os.path.join(PROJECT_ROOT, "admin.py")) as f:
            content = f.read()
        assert "/api/internal/lea-report" in content
        assert "X-Internal-Token" in content

    def test_admin_lea_reports_route_in_source(self):
        with open(os.path.join(PROJECT_ROOT, "admin.py")) as f:
            content = f.read()
        assert "/admin/lea-reports" in content
        assert "get_lea_reports" in content
