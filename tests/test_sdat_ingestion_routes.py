# SPDX-License-Identifier: AGPL-3.0-or-later
"""Operator and cron interfaces for scheduled SDAT ingestion."""

from unittest.mock import patch

import pytest

from app import create_app


@pytest.fixture
def application(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "admin-secret")
    return create_app(
        {
            "TESTING": True,
            "CRON_SECRET": "cron-secret",
            "RATELIMIT_STORAGE_URI": "memory://",
            "APP_BASE_URL": "http://localhost",
        },
        load_environment=False,
        check_database=False,
    )


def test_authorized_operator_can_configure_and_inspect_schedule(application):
    saved = {
        "territory": "dietikon",
        "enabled": True,
        "timezone": "Europe/Zurich",
        "local_time": "02:30",
        "max_attempts": 3,
        "retry_seconds": 30,
    }
    with (
        patch("database.upsert_sdat_ingestion_schedule", return_value=saved),
        patch("database.list_sdat_ingestion_schedules", return_value=[saved]),
    ):
        client = application.test_client()
        response = client.put(
            "/admin/sdat-schedules/dietikon",
            headers={"X-Admin-Token": "admin-secret"},
            json={
                "enabled": True,
                "timezone": "Europe/Zurich",
                "local_time": "02:30",
                "max_attempts": 3,
                "retry_seconds": 30,
            },
        )
        assert response.status_code == 200
        inspected = client.get(
            "/admin/sdat-schedules", headers={"X-Admin-Token": "admin-secret"}
        )
        assert inspected.get_json() == {"schedules": [saved]}


def test_schedule_rejects_invalid_timezone(application):
    response = application.test_client().put(
        "/admin/sdat-schedules/dietikon",
        headers={"X-Admin-Token": "admin-secret"},
        json={"enabled": True, "timezone": "secret/timezone", "local_time": "02:30"},
    )
    assert response.status_code == 400
    assert response.get_json() == {"error": "invalid_timezone"}


def test_schedule_rejects_delivery_directory_outside_sdat_root(application):
    response = application.test_client().put(
        "/admin/sdat-schedules/dietikon",
        headers={"X-Admin-Token": "admin-secret"},
        json={"enabled": True, "local_dir": "../../private"},
    )
    assert response.status_code == 400
    assert response.get_json() == {"error": "invalid_local_dir"}


def test_cron_runs_due_schedules_behind_cron_secret(application):
    schedules = [{"territory": "dietikon", "enabled": True}]
    with (
        patch("database.list_sdat_ingestion_schedules", return_value=schedules),
        patch(
            "sdat_ingestion.run_due",
            return_value={"due": 1, "succeeded": 1, "failed": 0, "locked": 0},
        ) as run_due,
    ):
        denied = application.test_client().post("/api/cron/import-sdat")
        assert denied.status_code == 403
        response = application.test_client().post(
            "/api/cron/import-sdat", headers={"X-Cron-Secret": "cron-secret"}
        )
    assert response.get_json()["succeeded"] == 1
    run_due.assert_called_once_with(schedules)


def test_authorized_operator_can_retry_failed_tenant_same_day(application):
    schedule = {"territory": "dietikon", "enabled": True}
    with (
        patch("database.list_sdat_ingestion_schedules", return_value=[schedule]),
        patch(
            "sdat_ingestion.run",
            return_value={"territory": "dietikon", "status": "success"},
        ) as run,
    ):
        response = application.test_client().post(
            "/admin/sdat-schedules/dietikon/run",
            headers={"X-Admin-Token": "admin-secret"},
        )
    assert response.status_code == 200
    run.assert_called_once_with("dietikon", schedule)
