# SPDX-License-Identifier: AGPL-3.0-or-later
"""Registration and emailed confirmation links against PostgreSQL."""

import uuid
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


@pytest.mark.integration
@pytest.mark.parametrize(
    "failure",
    [
        "syntax",
        "missing",
        "expired",
        "purpose",
        "deleted",
        "consumption",
        "verification",
    ],
)
def test_rejected_confirmation_has_no_success_effects(interest_client, failure):
    client, tasks, cluster = interest_client
    token = str(uuid.uuid4())
    assert test_interest_postgres.save_registration(verification_token=token)
    with db.get_connection() as conn, conn.cursor() as cur:
        if failure == "expired":
            cur.execute("UPDATE tokens SET expires_at = NOW() - INTERVAL '1 second'")
        elif failure == "purpose":
            cur.execute("UPDATE tokens SET token_type = 'unsubscribe'")
        elif failure == "deleted":
            cur.execute("DELETE FROM buildings")
        elif failure in ("consumption", "verification"):
            table = "tokens" if failure == "consumption" else "buildings"
            cur.execute("""CREATE FUNCTION reject_confirmation() RETURNS trigger AS $$
                BEGIN RAISE EXCEPTION 'injected write failure'; END;
                $$ LANGUAGE plpgsql""")
            cur.execute(
                f"CREATE TRIGGER reject_confirmation BEFORE UPDATE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION reject_confirmation()"
            )
    path_token = (
        "invalid"
        if failure == "syntax"
        else str(uuid.uuid4())
        if failure == "missing"
        else token
    )
    response = client.get(f"/confirm/{path_token}")
    assert response.status_code == (
        409 if failure in ("consumption", "verification") else 404
    )
    assert tasks == []
    cluster.assert_not_called()
    import app

    app.email_automation._send_email.assert_not_called()
    if failure != "deleted":
        assert db.get_building("interest-building")["verified"] is False
    if failure in ("consumption", "verification"):
        assert db.get_token(token) is not None


@pytest.mark.integration
@pytest.mark.parametrize("source", ["building", "coverage"])
@pytest.mark.parametrize("missing", ["bfs_number", "municipality_name"])
def test_confirmation_without_municipality_does_not_notify(
    interest_client, source, missing
):
    client, tasks, cluster = interest_client
    token = str(uuid.uuid4())
    if source == "building":
        assert test_interest_postgres.save_registration(verification_token=token)
        with db.get_connection() as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE buildings SET {missing} = NULL")
        path = f"/confirm/{token}"
    else:
        assert db.save_coverage_request(
            request_id=str(uuid.uuid4()),
            email="one@example.ch",
            address="Testweg 1",
            plz="4533",
            municipality_name="" if missing == "municipality_name" else "Riedholz",
            canton="SO",
            bfs_number=None if missing == "bfs_number" else 2554,
            roles=[],
            has_solar=False,
            verification_token=token,
        )
        path = f"/interest/confirm/{token}"
    assert client.get(path).status_code == 200
    assert client.get(path).status_code == 404
    import app

    app.email_automation._send_email.assert_not_called()
    assert len([task for task in tasks if task.target is cluster]) == (
        source == "building"
    )


@pytest.mark.integration
@pytest.mark.parametrize("failure", ["missing", "expired", "consumption"])
def test_unavailable_coverage_confirmation_has_no_effects(interest_client, failure):
    client, tasks, cluster = interest_client
    token = str(uuid.uuid4())
    assert db.save_coverage_request(
        request_id=str(uuid.uuid4()),
        email="one@example.ch",
        address="Testweg 1",
        plz="4533",
        municipality_name="Riedholz",
        canton="SO",
        bfs_number=2554,
        roles=[],
        has_solar=False,
        verification_token=token,
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        if failure == "expired":
            cur.execute(
                "UPDATE coverage_requests SET token_expires_at = NOW() - INTERVAL '1 second'"
            )
        elif failure == "consumption":
            cur.execute("""CREATE FUNCTION reject_coverage() RETURNS trigger AS $$
                BEGIN RAISE EXCEPTION 'injected write failure'; END;
                $$ LANGUAGE plpgsql""")
            cur.execute(
                "CREATE TRIGGER reject_coverage BEFORE UPDATE ON coverage_requests "
                "FOR EACH ROW EXECUTE FUNCTION reject_coverage()"
            )
    path_token = "missing" if failure == "missing" else token
    assert client.get(f"/interest/confirm/{path_token}").status_code == 404
    assert db.get_interest_count(2554) is None
    assert tasks == []
    cluster.assert_not_called()
    import app

    app.email_automation._send_email.assert_not_called()


@pytest.mark.integration
def test_coverage_intake_keeps_its_roles_validation_response(interest_client):
    client, tasks, _cluster = interest_client
    response = client.post(
        "/api/register_interest",
        json={
            "email": "one@example.ch",
            "plz": "4533",
            "municipality_name": "Riedholz",
            "roles": ["invalid-role"],
        },
    )
    assert response.status_code == 400
    assert response.json == {"error": "Bitte wählen Sie gültige Rollen aus."}
    assert tasks == []


@pytest.mark.integration
@pytest.mark.parametrize("plz", ["45330", "4533abc"])
def test_coverage_intake_rejects_the_complete_overlong_postal_code(
    interest_client, monkeypatch, plz
):
    client, _tasks, _cluster = interest_client
    import app

    send = MagicMock()
    monkeypatch.setattr(app, "send_email", send)
    response = client.post(
        "/api/register_interest",
        json={
            "email": "one@example.ch",
            "plz": plz,
            "municipality_name": "Riedholz",
        },
    )
    assert response.status_code == 400
    assert response.json == {"error": "Bitte geben Sie eine gültige Schweizer PLZ an."}
    assert db.get_operator_interest_records() == []
    send.assert_not_called()


@pytest.mark.integration
def test_verified_profile_rejects_another_email_without_changes(interest_client):
    client, tasks, _cluster = interest_client
    assert test_interest_postgres.save_registration(verified=True)
    before = db.get_building("interest-building")
    response = client.post(
        "/api/register_anonymous", json=_registration("other@example.ch")
    )
    assert response.status_code == 409
    assert db.get_building("interest-building") == before
    assert tasks == []


@pytest.mark.integration
def test_coverage_intake_keeps_saved_request_when_bounded_mail_delivery_fails(
    interest_client, monkeypatch
):
    client, _tasks, _cluster = interest_client
    import email_utils

    timeouts = []

    def unavailable_smtp(_host, _port, *, timeout=None):
        timeouts.append(timeout)
        raise TimeoutError("simulated SMTP timeout")

    monkeypatch.setattr(email_utils, "EMAIL_ENABLED", True)
    monkeypatch.setattr(email_utils.smtplib, "SMTP", unavailable_smtp)
    response = client.post(
        "/api/register_interest",
        json={
            "email": "one@example.ch",
            "plz": "4533",
            "municipality_name": "Riedholz",
        },
    )
    assert response.status_code == 202
    assert len(db.get_operator_interest_records()) == 1
    assert timeouts == [10]
