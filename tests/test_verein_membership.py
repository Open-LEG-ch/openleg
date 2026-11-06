# SPDX-License-Identifier: AGPL-3.0-or-later
"""Verein membership through HTTP, with native PostgreSQL and captured SMTP."""

import os
import re
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

import psycopg2
import pytest
from psycopg2.extras import RealDictCursor

from tests import test_i18n

app_module = test_i18n.app_module
isolated_environment = test_i18n.isolated_environment
web = test_i18n.web

# Capture the explicitly supplied scratch DSN before the app fixture clears env.
TEST_DSN = os.environ.get("OPENLEG_TEST_VEREIN_DATABASE_URL", "")
pytestmark = pytest.mark.integration


@pytest.fixture
def membership(web, monkeypatch):
    if not TEST_DSN:
        pytest.skip("Set OPENLEG_TEST_VEREIN_DATABASE_URL for native membership tests")
    import database
    import email_utils
    from store import verein

    schema = "verein_test_" + uuid.uuid4().hex
    with psycopg2.connect(TEST_DSN) as conn, conn.cursor() as cur:
        cur.execute('CREATE SCHEMA "' + schema + '"')

    @contextmanager
    def connection():
        conn = psycopg2.connect(
            TEST_DSN,
            options="-c search_path=" + schema,
            cursor_factory=RealDictCursor,
        )
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    monkeypatch.setattr(database, "get_connection", connection)
    with connection() as conn, conn.cursor() as cur:
        verein.create_tables(cur)
    mail = []
    monkeypatch.setattr(email_utils, "EMAIL_ENABLED", True)
    monkeypatch.setattr(
        email_utils,
        "send_email",
        lambda to, subject, body, **kwargs: mail.append((to, subject, body)) or True,
    )
    web.config.update(
        SITE_RELEASE_ENABLED=True,
        VEREIN_ACCEPTING_APPLICATIONS=True,
        VEREIN_FORMATION_DATE="2026-11-01",
        VEREIN_CONTACT_ADDRESS="Testpostfach Baden",
        VEREIN_PRIVACY_VERSION="reviewed-test-v1",
        VEREIN_STATUTES_URL="/static/test-statutes.pdf",
        VEREIN_DATA_CONTROLLER="Fictional test Verein",
    )
    try:
        yield web.test_client(), mail, verein
    finally:
        with psycopg2.connect(TEST_DSN) as conn, conn.cursor() as cur:
            cur.execute('DROP SCHEMA "' + schema + '" CASCADE')


def _csrf(client, path):
    page = client.get(path)
    assert page.status_code == 200
    return re.search(r'name="csrf_token" value="([^"]+)"', page.text)[1]


def _accepted(membership):
    client, mail, store = membership
    client.post(
        "/verein/beitreten",
        data={
            "csrf_token": _csrf(client, "/verein/beitreten"),
            "name": "Ada Muster",
            "email": "ada@example.org",
            "adult": "yes",
            "independent": "yes",
            "terms": "yes",
        },
    )
    client.get(re.search(r"http://localhost(/verein/bestaetigen/\S+)", mail[0][2])[1])
    reviewer_id = store.provision_reviewer(
        "reviewer@example.org", "Reviewer", "correct-horse-battery-staple"
    )
    client.post(
        "/verein/vorstand/login",
        data={
            "csrf_token": _csrf(client, "/verein/vorstand/login"),
            "email": "reviewer@example.org",
            "password": "correct-horse-battery-staple",
        },
    )
    identifier = re.search(
        r'data-application-id="([^"]+)"', client.get("/verein/vorstand/antraege").text
    )[1]
    path = "/verein/vorstand/antraege/" + identifier
    client.post(
        path,
        data={
            "csrf_token": _csrf(client, path),
            "decision": "accepted",
            "reason": "Bedingungen geprüft",
        },
    )
    return client, mail, store, reviewer_id, path


def test_revocation_and_csrf_protect_board_decisions(membership):
    client, _mail, store, reviewer_id, path = _accepted(membership)
    denied = client.post(path, data={"decision": "refused", "reason": "untrusted"})
    assert denied.status_code == 403
    store.revoke_reviewer(reviewer_id)
    assert client.get("/verein/vorstand/antraege").status_code == 401
    assert client.get(path).status_code == 401


def test_notifications_retry_without_repeating_admission(membership, monkeypatch):
    client, _mail, _store, _reviewer_id, path = _accepted(membership)
    import email_utils

    monkeypatch.setattr(email_utils, "send_email", lambda *args, **kwargs: False)
    queue = client.get("/verein/vorstand/antraege")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', queue.text)[1]
    failed = client.post(
        "/verein/vorstand/benachrichtigungen", data={"csrf_token": csrf}
    )
    assert failed.status_code == 503
    sent = []
    monkeypatch.setattr(
        email_utils,
        "send_email",
        lambda to, subject, body, **kwargs: sent.append((to, body)) or True,
    )
    retried = client.post(
        "/verein/vorstand/benachrichtigungen", data={"csrf_token": csrf}
    )
    assert retried.status_code == 303
    assert len(sent) == 1
    assert "CHF 50" in sent[0][1]
    client.post("/verein/vorstand/benachrichtigungen", data={"csrf_token": csrf})
    assert len(sent) == 1
    assert client.get(path).status_code == 200


def test_unpaid_member_gets_sixty_additional_days_after_sent_reminder(
    membership, monkeypatch
):
    from store import verein

    clock = [datetime(2026, 1, 1, tzinfo=UTC)]
    monkeypatch.setattr(verein, "now_utc", lambda: clock[0], raising=False)
    client, _mail, _store, _reviewer_id, path = _accepted(membership)
    csrf = _csrf(client, path)
    clock[0] = datetime(2026, 2, 1, tzinfo=UTC)
    reminder = client.post(path + "/reminder", data={"csrf_token": csrf})
    assert reminder.status_code == 303
    client.post("/verein/vorstand/benachrichtigungen", data={"csrf_token": csrf})
    clock[0] = datetime(2026, 4, 1, tzinfo=UTC)
    early = client.post(
        path + "/terminate",
        data={"csrf_token": csrf, "reason": "Beitrag trotz Erinnerung offen"},
    )
    assert early.status_code == 409
    clock[0] = datetime(2026, 4, 2, tzinfo=UTC)
    allowed = client.post(
        path + "/terminate",
        data={"csrf_token": csrf, "reason": "Beitrag trotz Erinnerung offen"},
    )
    assert allowed.status_code == 303
    assert "terminated" in client.get(path).text


def test_member_can_access_only_own_record_and_request_resignation(membership):
    client, mail, _store, _reviewer_id, path = _accepted(membership)
    requested = client.post(
        "/verein/mitglied/zugang",
        data={
            "csrf_token": _csrf(client, "/verein/mitglied/zugang"),
            "email": "ada@example.org",
        },
    )
    assert requested.status_code == 202
    access = re.search(r"http://localhost(/verein/mitglied/zugang/\S+)", mail[-1][2])[1]
    owner = client.application.test_client()
    opened = owner.get(access)
    assert opened.status_code == 303
    assert owner.get(access).status_code == 410
    profile = owner.get("/verein/mitglied")
    assert profile.status_code == 200
    assert "Ada Muster" in profile.text
    assert "CHF 50" in profile.text
    assert owner.get(path).status_code == 401
    resignation = owner.post(
        "/verein/mitglied/austritt",
        data={
            "csrf_token": _csrf(owner, "/verein/mitglied"),
            "reason": "Ich möchte austreten",
        },
    )
    assert resignation.status_code == 303
    assert "Austrittsantrag" in owner.get("/verein/mitglied").text


def test_application_is_confirmed_before_private_board_review(membership):
    client, mail, _store = membership
    csrf = _csrf(client, "/verein/beitreten")
    response = client.post(
        "/verein/beitreten",
        data={
            "csrf_token": csrf,
            "name": "Ada Muster",
            "email": "ada@example.org",
            "adult": "yes",
            "independent": "yes",
            "terms": "yes",
        },
    )
    assert response.status_code == 202
    assert "Mitglied" not in response.json.get("status", "")
    assert len(mail) == 1
    confirmation = re.search(r"http://localhost(/verein/bestaetigen/\S+)", mail[0][2])[
        1
    ]
    confirmed = client.get(confirmation)
    assert confirmed.status_code == 200
    assert confirmed.headers["Cache-Control"] == "no-store"
    assert "Prüfung" in confirmed.text
    assert client.get("/verein/vorstand/antraege").status_code == 401


def test_board_admission_creates_one_membership_and_one_fee(membership):
    client, mail, store = membership
    client.post(
        "/verein/beitreten",
        data={
            "csrf_token": _csrf(client, "/verein/beitreten"),
            "name": "Ada Muster",
            "email": "ada@example.org",
            "adult": "yes",
            "independent": "yes",
            "terms": "yes",
        },
    )
    confirmation = re.search(r"http://localhost(/verein/bestaetigen/\S+)", mail[0][2])[
        1
    ]
    client.get(confirmation)
    store.provision_reviewer(
        "reviewer@example.org", "Reviewer", "correct-horse-battery-staple"
    )
    login = client.post(
        "/verein/vorstand/login",
        data={
            "csrf_token": _csrf(client, "/verein/vorstand/login"),
            "email": "reviewer@example.org",
            "password": "correct-horse-battery-staple",
        },
    )
    assert login.status_code == 303
    queue = client.get("/verein/vorstand/antraege")
    assert "Ada Muster" in queue.text
    application_id = re.search(r'data-application-id="([^"]+)"', queue.text)[1]
    decision_path = "/verein/vorstand/antraege/" + application_id
    csrf = _csrf(client, decision_path)
    accepted = client.post(
        decision_path,
        data={
            "csrf_token": csrf,
            "decision": "accepted",
            "reason": "Bedingungen geprüft",
        },
    )
    assert accepted.status_code == 303
    repeated = client.post(
        decision_path,
        data={
            "csrf_token": csrf,
            "decision": "accepted",
            "reason": "Bedingungen geprüft",
        },
    )
    assert repeated.status_code == 409
    detail = client.get(decision_path)
    assert "CHF 50" in detail.text
    assert "accepted" in detail.text
    assert client.get("/admin").status_code in {401, 403, 404}
