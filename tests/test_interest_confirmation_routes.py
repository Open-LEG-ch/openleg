# SPDX-License-Identifier: AGPL-3.0-or-later
"""Registration and emailed confirmation links against PostgreSQL."""

from unittest.mock import MagicMock
from urllib.parse import urlsplit

import pytest

import database as db
from tests import test_interest_postgres

interest_database = test_interest_postgres.interest_database


@pytest.fixture
def interest_client(interest_database, monkeypatch):
    for name, value in {
        "REDIS_URL": "memory://",
        "CRON_SECRET": "test-cron-secret",
        "APP_BASE_URL": "http://localhost:5003",
        "PUBLIC_SITE_URL": "https://openleg.ch",
        "SESSION_COOKIE_SECURE": "false",
        "ALLOWED_HOSTS": "localhost",
        "SECRET_KEY": "interest-test-key",
        "ADMIN_EMAIL": "admin@example.ch",
        "SESSION_COOKIE_SAMESITE": "Lax",
        "PERMANENT_SESSION_LIFETIME": "3600",
        "DASHBOARD_ACCESS_TOKEN_TTL_SECONDS": "900",
        "DASHBOARD_EMAIL_TOKEN_TTL_SECONDS": "86400",
    }.items():
        monkeypatch.setenv(name, value)
    import app

    monkeypatch.setattr(app, "db", db)
    monkeypatch.setattr(app.limiter, "enabled", False)
    tasks = []

    class CapturedThread:
        def __init__(self, *, target, args, daemon):
            self.target, self.args, self.daemon = target, args, daemon

        def start(self):
            tasks.append(self)

    monkeypatch.setattr(app.threading, "Thread", CapturedThread)
    cluster = MagicMock()
    monkeypatch.setattr(app, "run_full_ml_task", cluster)
    # Mail delivery and clustering are the external effects under observation.
    monkeypatch.setattr(app, "send_confirmation_email", MagicMock())
    monkeypatch.setattr(
        app.email_automation, "_send_email", MagicMock(return_value=True)
    )
    application = app.create_app(
        {"TESTING": True}, check_database=False, load_environment=False
    )
    return application.test_client(), tasks, cluster


def _registration(email):
    return {
        "email": email,
        "profile": {
            "building_id": "interest-building",
            "address": "Testweg 1",
            "lat": 47.2,
            "lon": 8.2,
            "bfs_number": 2554,
            "municipality_name": "Riedholz",
            "annual_consumption_kwh": 4500,
            "potential_pv_kwp": 0,
        },
        "consents": {},
    }


@pytest.mark.integration
def test_emailed_link_confirms_only_its_email_and_defers_clustering(interest_client):
    client, tasks, cluster = interest_client
    assert (
        client.post(
            "/api/register_anonymous", json=_registration("old@example.ch")
        ).status_code
        == 200
    )
    old_path = urlsplit(tasks[-1].args[1]).path
    assert (
        client.post(
            "/api/register_anonymous", json=_registration("new@example.ch")
        ).status_code
        == 200
    )
    new_path = urlsplit(tasks[-1].args[1]).path

    assert client.get(old_path).status_code == 404
    assert db.get_building("interest-building")["verified"] is False
    response = client.get(new_path)
    assert response.status_code == 200
    assert "bestätigt" in response.get_data(as_text=True)
    assert db.get_building("interest-building")["verified"] is True
    cluster.assert_not_called()
    cluster_tasks = [task for task in tasks if task.target is cluster]
    assert len(cluster_tasks) == 1
    assert cluster_tasks[0].args == ("interest-building", "zurich")
    assert cluster_tasks[0].daemon is True
    assert client.get(new_path).status_code == 404
