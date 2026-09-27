# SPDX-License-Identifier: AGPL-3.0-or-later
"""Mutual consent through private HTTP routes and real PostgreSQL."""

import re
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import MagicMock

import pytest

import database as db
from tests.test_dashboard_access_routes import _set_session, app_module
from tests.test_interest_postgres import interest_database

# Reuse fixtures, keeping the real store and authorization path intact.
__all__ = ["app_module", "interest_database"]
pytestmark = pytest.mark.integration


@pytest.fixture
def contacts(interest_database, app_module, monkeypatch):
    for bid, bfs, verified, consent in [
        ("alice", 261, True, True),
        ("bob", 261, True, True),
        ("outsider", 261, True, True),
        ("elsewhere", 999, True, True),
        ("unverified", 261, False, True),
        ("private", 261, True, False),
    ]:
        assert db.save_building(
            bid,
            f"{bid}@example.ch",
            {
                "address": f"Secret {bid} address",
                "lat": 47.2,
                "lon": 8.2,
                "bfs_number": bfs,
            },
            {"share_with_neighbors": consent},
            phone=f"phone-{bid}",
            verified=verified,
        )
    sent = MagicMock(return_value=True)
    monkeypatch.setattr(app_module, "send_email", sent)
    client = app_module.web.test_client()
    _set_session(client, "alice")
    return client, sent


def _post(client, path, **data):
    return client.post(path, data={"csrf_token": "csrf-secret", **data})


def _request_bob(client):
    # Opaque candidate handles must not contain identifiers or contact data.
    page = client.get("/dashboard/contacts")
    assert page.status_code == 200
    tokens = re.findall(r'name="candidate" value="([a-f0-9]+)"', page.text)
    assert len(tokens) == 2
    response = _post(
        client, "/dashboard/contacts", candidate=tokens[0], share_email="yes"
    )
    assert response.status_code == 302
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, recipient_id FROM contact_requests")
        row = cur.fetchone()
        assert row["recipient_id"] == "bob"
        return str(row["id"])


def test_mutual_acceptance_reveals_only_explicit_fields(contacts):
    client, sent = contacts
    rid = _request_bob(client)
    for actor in ("alice", "bob"):
        _set_session(client, actor)
        page = client.get("/dashboard/contacts")
        assert "alice@example.ch" not in page.text
        assert "bob@example.ch" not in page.text
        assert "Secret bob address" not in page.text
        assert page.headers["Cache-Control"] == "no-store"
        assert page.headers["Referrer-Policy"] == "no-referrer"
    assert (
        _post(
            client, f"/dashboard/contacts/{rid}/accept", share_phone="yes"
        ).status_code
        == 302
    )
    assert "alice@example.ch" in client.get("/dashboard/contacts").text
    assert "phone-alice" not in client.get("/dashboard/contacts").text
    _set_session(client, "alice")
    page = client.get("/dashboard/contacts")
    assert "phone-bob" in page.text
    assert "bob@example.ch" not in page.text
    assert sent.call_count == 4
    for call in sent.call_args_list:
        assert call.kwargs == {"private": True}
        assert call.args[0] in {"alice@example.ch", "bob@example.ch"}
        assert "@example.ch" not in call.args[2]
        assert "phone-" not in call.args[2]


@pytest.mark.parametrize("action", ["accept", "decline", "revoke"])
def test_outsiders_cannot_transition_or_read_requests(contacts, action):
    client, sent = contacts
    rid = _request_bob(client)
    _set_session(client, "outsider")
    assert rid not in client.get("/dashboard/contacts").text
    assert (
        _post(
            client, f"/dashboard/contacts/{rid}/{action}", share_email="yes"
        ).status_code
        == 403
    )
    assert sent.call_count == 2


@pytest.mark.parametrize("action", ["accept", "decline"])
def test_requester_cannot_act_for_recipient(contacts, action):
    client, _ = contacts
    rid = _request_bob(client)
    assert (
        _post(
            client, f"/dashboard/contacts/{rid}/{action}", share_email="yes"
        ).status_code
        == 403
    )


@pytest.mark.parametrize("actor", ["alice", "bob"])
def test_either_participant_can_revoke_and_contact_values_are_erased(contacts, actor):
    client, _ = contacts
    rid = _request_bob(client)
    _set_session(client, "bob")
    assert (
        _post(
            client, f"/dashboard/contacts/{rid}/accept", share_phone="yes"
        ).status_code
        == 302
    )
    _set_session(client, actor)
    assert _post(client, f"/dashboard/contacts/{rid}/revoke").status_code == 302
    for participant in ("alice", "bob"):
        _set_session(client, participant)
        page = client.get("/dashboard/contacts")
        assert "Widerrufen" in page.text
        assert "alice@example.ch" not in page.text
        assert "phone-bob" not in page.text
    assert (
        _post(
            client, f"/dashboard/contacts/{rid}/accept", share_email="yes"
        ).status_code
        == 403
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM contact_requests WHERE id = %s", (rid,))
        row = cur.fetchone()
        assert row["requester_fields"] == row["recipient_fields"] == {}
        assert row["accepted_at"] and row["ended_at"]
        cur.execute(
            "SELECT action, consent_fields, actor_id, occurred_at FROM contact_request_events WHERE request_id = %s ORDER BY id",
            (rid,),
        )
        events = cur.fetchall()
        assert [e["action"] for e in events] == ["requested", "accepted", "revoked"]
        assert [e["consent_fields"] for e in events] == [["email"], ["phone"], []]
        assert events[-1]["actor_id"] == actor
        assert all(e["occurred_at"] for e in events)


def test_decline_is_terminal_and_cannot_be_replayed(contacts):
    client, sent = contacts
    rid = _request_bob(client)
    _set_session(client, "bob")
    assert _post(client, f"/dashboard/contacts/{rid}/decline").status_code == 302
    assert "Abgelehnt" in client.get("/dashboard/contacts").text
    assert (
        _post(
            client, f"/dashboard/contacts/{rid}/accept", share_email="yes"
        ).status_code
        == 403
    )
    assert _post(client, f"/dashboard/contacts/{rid}/decline").status_code == 403
    assert sent.call_count == 4


@pytest.mark.parametrize("action", ["accept", "decline", "revoke"])
def test_expired_request_cannot_transition(contacts, action):
    client, sent = contacts
    rid = _request_bob(client)
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE contact_requests SET expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second' WHERE id = %s",
            (rid,),
        )
    _set_session(client, "bob")
    assert (
        _post(
            client, f"/dashboard/contacts/{rid}/{action}", share_email="yes"
        ).status_code
        == 403
    )
    assert "Abgelaufen" in client.get("/dashboard/contacts").text
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT status, requester_fields FROM contact_requests WHERE id = %s",
            (rid,),
        )
        assert dict(cur.fetchone()) == {"status": "expired", "requester_fields": {}}
    assert sent.call_count == 2


@pytest.mark.parametrize("actor", ["elsewhere", "unverified", "private", "outsider"])
def test_candidate_handles_are_bound_to_eligible_viewer(contacts, actor):
    client, sent = contacts
    page = client.get("/dashboard/contacts")
    token = re.findall(r'name="candidate" value="([a-f0-9]+)"', page.text)[0]
    _set_session(client, actor)
    assert (
        _post(
            client, "/dashboard/contacts", candidate=token, share_email="yes"
        ).status_code
        == 403
    )
    assert not sent.called


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE buildings SET verified = FALSE WHERE building_id = 'bob'",
        "UPDATE buildings SET bfs_number = 999 WHERE building_id = 'bob'",
        "UPDATE buildings SET phone = 'replacement' WHERE building_id = 'bob'",
        "UPDATE consents SET share_with_neighbors = FALSE WHERE building_id = 'bob'",
        "UPDATE consents SET consent_timestamp = CURRENT_TIMESTAMP WHERE building_id = 'bob'",
    ],
)
@pytest.mark.parametrize("accepted", [False, True])
def test_profile_changes_invalidate_pending_and_accepted_grants(
    contacts, change, accepted
):
    client, _ = contacts
    rid = _request_bob(client)
    _set_session(client, "bob")
    if accepted:
        assert (
            _post(
                client, f"/dashboard/contacts/{rid}/accept", share_email="yes"
            ).status_code
            == 302
        )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(change)
    if not accepted:
        assert (
            _post(
                client, f"/dashboard/contacts/{rid}/accept", share_email="yes"
            ).status_code
            == 403
        )
    for actor in ("alice", "bob"):
        _set_session(client, actor)
        page = client.get("/dashboard/contacts")
        assert "Widerrufen" in page.text
        assert "alice@example.ch" not in page.text
        assert "bob@example.ch" not in page.text


def test_no_consent_csrf_session_or_valid_handle_fails_closed(contacts):
    client, sent = contacts
    page = client.get("/dashboard/contacts")
    token = re.findall(r'name="candidate" value="([a-f0-9]+)"', page.text)[0]
    assert _post(client, "/dashboard/contacts", candidate=token).status_code == 403
    assert (
        _post(
            client, "/dashboard/contacts", candidate="bogus", share_email="yes"
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/dashboard/contacts", data={"candidate": token, "share_email": "yes"}
        ).status_code
        == 400
    )
    with client.session_transaction() as state:
        state.clear()
    assert client.get("/dashboard/contacts").status_code == 401
    assert (
        _post(
            client, "/dashboard/contacts", candidate=token, share_email="yes"
        ).status_code
        == 401
    )
    assert not sent.called


def test_duplicate_and_reverse_requests_do_not_repeat_notifications(contacts):
    client, sent = contacts
    token = re.findall(
        r'name="candidate" value="([a-f0-9]+)"', client.get("/dashboard/contacts").text
    )[0]
    _request_bob(client)
    assert (
        _post(
            client, "/dashboard/contacts", candidate=token, share_email="yes"
        ).status_code
        == 403
    )
    _set_session(client, "bob")
    tokens = re.findall(
        r'name="candidate" value="([a-f0-9]+)"', client.get("/dashboard/contacts").text
    )
    assert len(tokens) == 1  # Only the unrelated third household remains.
    assert sent.call_count == 2


@pytest.mark.parametrize("participant", ["alice", "bob"])
def test_profile_deletion_cascades_request_and_audit(contacts, participant):
    client, _ = contacts
    _request_bob(client)
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM buildings WHERE building_id = %s", (participant,))
        for table in ("contact_requests", "contact_request_events"):
            cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
            assert cur.fetchone()["n"] == 0


def test_notification_failure_keeps_request_and_reports_delivery_problem(contacts):
    client, sent = contacts
    sent.side_effect = RuntimeError("provider failure")
    rid = _request_bob(client)
    assert sent.call_count == 2
    assert rid in client.get("/dashboard/contacts").text
    assert (
        "Mindestens eine E-Mail"
        in client.get("/dashboard/contacts?notice=mail-failed").text
    )


def test_private_notification_sender_omits_recipient_from_logs(monkeypatch, caplog):
    import email_utils

    monkeypatch.setattr(email_utils, "EMAIL_ENABLED", True)
    smtp = MagicMock(side_effect=RuntimeError("failure for hidden@example.ch"))
    monkeypatch.setattr(email_utils.smtplib, "SMTP", smtp)
    assert (
        email_utils.send_email(
            "hidden@example.ch", "Kontaktanfrage", "Private notice", private=True
        )
        is False
    )
    assert "hidden@example.ch" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_database_failure_never_becomes_empty_success(contacts, monkeypatch):
    client, sent = contacts

    def unavailable():
        raise RuntimeError("storage failure")

    monkeypatch.setattr(db, "get_connection", unavailable)
    assert client.get("/dashboard/contacts").status_code == 503
    assert (
        _post(
            client, "/dashboard/contacts", candidate="a" * 64, share_email="yes"
        ).status_code
        == 503
    )
    assert not sent.called


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE buildings SET verified = FALSE WHERE building_id = 'bob'",
        "UPDATE buildings SET bfs_number = 999 WHERE building_id = 'bob'",
        "UPDATE consents SET share_with_neighbors = FALSE WHERE building_id = 'bob'",
    ],
)
def test_create_rechecks_recipient_after_candidate_list_was_rendered(contacts, change):
    client, sent = contacts
    token = re.findall(
        r'name="candidate" value="([a-f0-9]+)"', client.get("/dashboard/contacts").text
    )[0]
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(change)
    assert (
        _post(
            client, "/dashboard/contacts", candidate=token, share_email="yes"
        ).status_code
        == 403
    )
    assert not sent.called


def test_failed_audit_insert_rolls_back_request_and_sends_nothing(contacts):
    client, sent = contacts
    token = re.findall(
        r'name="candidate" value="([a-f0-9]+)"', client.get("/dashboard/contacts").text
    )[0]
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE FUNCTION reject_contact_event() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'injected audit failure'; END;
            $$ LANGUAGE plpgsql;
            CREATE TRIGGER reject_event BEFORE INSERT ON contact_request_events
            FOR EACH ROW EXECUTE FUNCTION reject_contact_event();
        """)
    assert (
        _post(
            client, "/dashboard/contacts", candidate=token, share_email="yes"
        ).status_code
        == 503
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM contact_requests")
        assert cur.fetchone()["n"] == 0
    assert not sent.called


def test_simultaneous_reverse_requests_create_one_consent_record(contacts):
    client, sent = contacts
    clients = [client, client.application.test_client()]
    tokens = []
    for actor, browser in zip(("alice", "bob"), clients):
        _set_session(browser, actor)
        tokens.append(
            re.findall(
                r'name="candidate" value="([a-f0-9]+)"',
                browser.get("/dashboard/contacts").text,
            )[0]
        )
    ready = Barrier(2)

    def submit(index):
        ready.wait(timeout=5)
        return _post(
            clients[index],
            "/dashboard/contacts",
            candidate=tokens[index],
            share_email="yes",
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sorted(workers.map(submit, (0, 1))) == [302, 403]
    assert sent.call_count == 2
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT status FROM contact_requests")
        assert [row["status"] for row in cur.fetchall()] == ["pending"]
        cur.execute("SELECT action FROM contact_request_events")
        assert [row["action"] for row in cur.fetchall()] == ["requested"]
