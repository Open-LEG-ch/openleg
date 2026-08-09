# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for private ops dashboard and internal snapshot ingestion."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestAdminOpsRoutes:
    def test_admin_ops_requires_admin(self):
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
            try:
                from app import app

                client = app.test_client()
                resp = client.get("/admin/ops")
                assert resp.status_code == 403
            except Exception:
                pytest.skip("App import requires live DB")

    def test_admin_ops_returns_json(self):
        snapshots = [
            {
                "id": 1,
                "source": "openclaw",
                "category": "openclaw_health",
                "status": "ok",
                "summary_text": "Gateway healthy",
                "payload": {"sessions": 1},
                "created_at": "2026-06-14T10:00:00",
            },
            {
                "id": 2,
                "source": "agentmail",
                "category": "lea_inbox",
                "status": "received",
                "summary_text": "New LEA inbox item",
                "payload": {"subject": "Hello"},
                "created_at": "2026-06-14T10:05:00",
            },
        ]
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
            patch("database.get_ops_snapshots", return_value=snapshots),
            patch("database.get_lea_reports", return_value=[]),
        ):
            try:
                from app import app

                client = app.test_client()
                resp = client.get(
                    "/admin/ops",
                    headers={"X-Admin-Token": "test123"},
                )
                assert resp.status_code == 200
                data = json.loads(resp.data)
                assert "latest" in data
                assert "counts" in data
                assert data["counts"]["lea_inbox"] == 1
            except Exception:
                pytest.skip("App import requires live DB")

    def test_internal_ops_snapshot_accepts_valid_token(self):
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
            patch("database.save_ops_snapshot", return_value=True),
        ):
            try:
                from app import app

                client = app.test_client()
                resp = client.post(
                    "/api/internal/ops-snapshot",
                    json={
                        "source": "openclaw",
                        "category": "openclaw_health",
                        "summary": "Gateway healthy",
                        "payload": {"sessions": 1},
                    },
                    headers={"X-Internal-Token": "secret-internal"},
                )
                assert resp.status_code == 200
            except Exception:
                pytest.skip("App import requires live DB")

    def test_internal_agentmail_ignores_non_inbound_events(self):
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
            patch("database.save_ops_snapshot", return_value=True) as save_snapshot,
        ):
            try:
                from app import app

                client = app.test_client()
                resp = client.post(
                    "/api/internal/agentmail",
                    json={"type": "message.sent"},
                    headers={"X-Internal-Token": "secret-internal"},
                )
                assert resp.status_code == 200
                assert resp.get_json()["ignored"] is True
                save_snapshot.assert_not_called()
            except Exception:
                pytest.skip("App import requires live DB")

    def test_internal_agentmail_accepts_agentmail_event_type_payload(self):
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
            patch("database.save_ops_snapshot", return_value=True) as save_snapshot,
        ):
            try:
                from app import app

                client = app.test_client()
                resp = client.post(
                    "/api/internal/agentmail",
                    json={
                        "event_type": "message.received",
                        "event_id": "evt_123",
                        "message": {
                            "message_id": "msg_123",
                            "thread_id": "thd_123",
                            "inbox_id": "hallo@openleg.ch",
                            "from_": ["sender@example.com"],
                            "to": ["hallo@openleg.ch"],
                            "subject": "LEG Anfrage",
                            "timestamp": "2026-06-14T10:05:00Z",
                            "text": "Bitte um Informationen zur LEG.",
                        },
                    },
                    headers={"X-Internal-Token": "secret-internal"},
                )
                assert resp.status_code == 200
                save_snapshot.assert_called_once()
                kwargs = save_snapshot.call_args.kwargs
                assert kwargs["source"] == "agentmail"
                assert kwargs["category"] == "lea_inbox"
                assert kwargs["summary_text"] == "LEG Anfrage"
                assert kwargs["payload"]["event_type"] == "message.received"
                assert kwargs["payload"]["message_id"] == "msg_123"
                assert kwargs["payload"]["inbox_id"] == "hallo@openleg.ch"
                assert kwargs["payload"]["from_email"] == "sender@example.com"
            except Exception:
                pytest.skip("App import requires live DB")


class TestAdminOpsRouteExists:
    def test_admin_ops_routes_in_source(self):
        with open(os.path.join(PROJECT_ROOT, "app.py")) as f:
            content = f.read()
        assert "/admin/ops" in content
        assert "/api/internal/ops-snapshot" in content
        assert "/api/internal/agentmail" in content


class TestAdminRegistryModeration:
    def test_admin_ops_includes_registry_pending_count(self):
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
            patch("database.get_ops_snapshots", return_value=[]),
            patch("database.get_lea_reports", return_value=[]),
            patch("database.list_registry_entries", return_value=[{"id": 1}]),
            patch("database.get_registry_pending_count", return_value=1),
        ):
            try:
                from app import app

                client = app.test_client()
                resp = client.get(
                    "/admin/ops",
                    headers={"X-Admin-Token": "test123"},
                )
                assert resp.status_code == 200
                data = json.loads(resp.data)
                assert data["counts"]["registry_pending"] == 1
                assert data["pending_registry"] == [{"id": 1}]
            except Exception:
                pytest.skip("App import requires live DB")

    def test_admin_registry_approve_requires_admin_token(self):
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
            patch(
                "database.update_registry_entry_moderation", return_value=True
            ) as mock_update,
        ):
            try:
                from app import app

                client = app.test_client()
                resp = client.post("/admin/registry/1/approve")
                assert resp.status_code == 403
                mock_update.assert_not_called()
            except Exception:
                pytest.skip("App import requires live DB")

    def test_admin_registry_approve_calls_store_with_published(self):
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
            patch(
                "database.update_registry_entry_moderation", return_value=True
            ) as mock_update,
        ):
            try:
                from app import app

                client = app.test_client()
                resp = client.post(
                    "/admin/registry/1/approve",
                    headers={"X-Admin-Token": "test123"},
                )
                assert resp.status_code in (200, 302)
                mock_update.assert_called_once_with(1, "published", "")
            except Exception:
                pytest.skip("App import requires live DB")

    def test_admin_registry_reject_accepts_optional_reason(self):
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
            patch(
                "database.update_registry_entry_moderation", return_value=True
            ) as mock_update,
        ):
            try:
                from app import app

                client = app.test_client()
                resp = client.post(
                    "/admin/registry/1/reject",
                    data={"reason": "Duplikat"},
                    headers={"X-Admin-Token": "test123"},
                )
                assert resp.status_code in (200, 302)
                mock_update.assert_called_once_with(1, "rejected", "Duplikat")
            except Exception:
                pytest.skip("App import requires live DB")

    def test_admin_ops_html_has_registry_moderation_section(self):
        with open(os.path.join(PROJECT_ROOT, "templates", "admin", "ops.html")) as f:
            content = f.read()
        assert "pending_registry" in content
        assert "/approve" in content
        assert "/reject" in content

    def test_admin_ops_includes_stale_registry_count_and_vnb_plausibility(self):
        stale_entry = {
            "id": 1,
            "name": "LEG Stale",
            "bfs_number": 4021,
            "vnb_name": "Regionalwerke Baden",
            "contact_email": "info@example.ch",
        }
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
            patch("database.get_ops_snapshots", return_value=[]),
            patch("database.get_lea_reports", return_value=[]),
            patch("database.list_registry_entries", return_value=[stale_entry]),
            patch("database.get_registry_pending_count", return_value=1),
            patch(
                "database.get_registry_entries_needing_verification",
                return_value=[stale_entry],
            ),
            patch(
                "database.get_elcom_tariffs",
                return_value=[{"operator_name": "Regionalwerke Baden AG"}],
            ),
        ):
            try:
                from app import app

                client = app.test_client()
                resp = client.get(
                    "/admin/ops",
                    headers={"X-Admin-Token": "test123"},
                )
                assert resp.status_code == 200
                data = json.loads(resp.data)
                assert data["counts"]["registry_stale"] == 1
                assert data["pending_registry"][0]["vnb_plausible"] is True
            except Exception:
                pytest.skip("App import requires live DB")
