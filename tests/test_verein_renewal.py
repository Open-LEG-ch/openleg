# SPDX-License-Identifier: AGPL-3.0-or-later
"""Anniversary years, immediate receipt of resignation and period-bound receipts."""

from datetime import UTC, datetime

import pytest

from tests import test_verein_membership

_accepted = test_verein_membership._accepted
_csrf = test_verein_membership._csrf
app_module = test_verein_membership.app_module
isolated_environment = test_verein_membership.isolated_environment
membership = test_verein_membership.membership
web = test_verein_membership.web
pytestmark = pytest.mark.integration


def test_resignation_takes_effect_on_receipt_without_board_approval(
    membership, monkeypatch
):
    client, _mail, store, _reviewer_id, path = _accepted(membership)
    import database

    # Upgrade a pre-receipt-status schema without losing its actual records.
    with database.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "ALTER TABLE verein_member_requests DROP CONSTRAINT verein_member_requests_status_check"
        )
        cur.execute(
            "ALTER TABLE verein_member_requests ADD CONSTRAINT verein_member_requests_status_check CHECK(status IN ('pending','approved','dismissed'))"
        )
        store.create_tables(cur)
    now = datetime(2026, 11, 6, 12, 34, tzinfo=UTC)
    monkeypatch.setattr(store, "now_utc", lambda: now)
    access = store.request_applicant_access("ada@example.org")
    owner_token = store.consume_applicant_access(access["token"])
    owner = client.application.test_client()
    with owner.session_transaction() as session:
        session["verein_applicant_session"] = owner_token
    csrf = _csrf(owner, "/verein/mitglied")
    assert (
        owner.post("/verein/mitglied/austritt", data={"reason": ""}).status_code == 403
    )
    assert store.applicant_profile(owner_token)["member_status"] == "active"
    response = owner.post(
        "/verein/mitglied/austritt", data={"csrf_token": csrf, "reason": ""}
    )
    assert response.status_code == 303
    record = store.applicant_profile(owner_token)
    assert record["member_status"] == "resigned"
    assert record["decision_at"] == now
    assert record["amount_chf"] == 50
    with client.session_transaction() as session:
        assert not store.list_member_requests(session["verein_session"])
        notices = store.pending_notifications(session["verein_session"])
    assert [item["kind"] for item in notices] == ["resignation_received"]
    assert (
        owner.post("/verein/mitglied/austritt", data={"csrf_token": csrf}).status_code
        == 409
    )
    assert client.get(path).status_code == 200


def test_annual_dues_follow_original_admission_anniversary_without_leap_drift(
    membership, monkeypatch
):
    import database
    from store import verein

    now = datetime(2024, 2, 29, 12, tzinfo=UTC)
    monkeypatch.setattr(verein, "now_utc", lambda: now)
    client, _mail, store, _reviewer_id, _path = _accepted(membership)
    with client.session_transaction() as session:
        board = session["verein_session"]
    now = datetime(2027, 3, 1, tzinfo=UTC)
    assert store.renew_dues(board) == 3
    assert store.renew_dues(board) == 0
    with database.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT period_start,due_on,amount_chf FROM verein_dues ORDER BY period_start"
        )
        dues = cur.fetchall()
    assert [row["period_start"].isoformat() for row in dues] == [
        "2024-02-29",
        "2025-02-28",
        "2026-02-28",
        "2027-02-28",
    ]
    assert all(row["amount_chf"] == 50 for row in dues)
    now = datetime(2028, 2, 28, 12, tzinfo=UTC)
    assert store.renew_dues(board) == 0
    now = datetime(2028, 2, 29, 12, tzinfo=UTC)
    assert store.renew_dues(board) == 1
    notices = store.pending_notifications(board)
    assert (
        next(item for item in notices if item["kind"] == "accepted")[
            "due_on"
        ].isoformat()
        == "2024-03-30"
    )
    assert [
        item["due_on"].isoformat() for item in notices if item["kind"] == "renewal"
    ] == ["2025-03-30", "2026-03-30", "2027-03-30", "2028-03-30"]


def test_receipts_bind_to_one_period_and_resigned_members_do_not_renew(
    membership, monkeypatch
):
    import database
    from store import verein

    now = datetime(2025, 6, 1, 12, tzinfo=UTC)
    monkeypatch.setattr(verein, "now_utc", lambda: now)
    client, _mail, store, _reviewer_id, path = _accepted(membership)
    with client.session_transaction() as session:
        board = session["verein_session"]
    now = datetime(2026, 6, 1, 12, tzinfo=UTC)
    store.renew_dues(board)
    with database.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM verein_dues ORDER BY period_start")
        old, current = cur.fetchall()
    store.record_payment(
        board,
        path.rsplit("/", 1)[1],
        dues_id=str(old["id"]),
        amount_chf="50",
        source="test-bank",
        reference="previous-year",
        evidence="Actual test source",
    )
    detail = client.get(path)
    assert str(old["id"]) in detail.text and str(current["id"]) in detail.text
    access = store.request_applicant_access("ada@example.org")
    owner = store.consume_applicant_access(access["token"])
    record = store.applicant_profile(owner)
    assert len(record["dues"]) == 2
    assert sum(item["paid_at"] is not None for item in record["dues"]) == 1
    with pytest.raises(store.VereinDenied):
        store.record_payment(
            board,
            path.rsplit("/", 1)[1],
            dues_id=str(current["id"]),
            amount_chf="50",
            source="test-bank",
            reference="previous-year",
            evidence="Actual test source",
        )
    store.submit_member_request(owner, "resignation", "")
    now = datetime(2027, 6, 1, 12, tzinfo=UTC)
    assert store.renew_dues(board) == 0
    with pytest.raises(store.VereinDenied):
        store.request_reminder(
            board, path.rsplit("/", 1)[1], dues_id=str(current["id"])
        )


def test_http_renewal_creates_once_and_sends_the_bound_year(membership, monkeypatch):
    from store import verein

    now = datetime(2025, 6, 1, 12, tzinfo=UTC)
    monkeypatch.setattr(verein, "now_utc", lambda: now)
    client, mail, _store, _reviewer_id, _path = _accepted(membership)
    csrf = _csrf(client, "/verein/vorstand/antraege")
    now = datetime(2026, 6, 1, 12, tzinfo=UTC)
    path = "/verein/vorstand/beitraege/erneuern"
    assert client.post(path).status_code == 403
    assert client.post(path, data={"csrf_token": csrf}).status_code == 303
    assert client.post(path, data={"csrf_token": csrf}).status_code == 303
    sent_before = len(mail)
    assert (
        client.post(
            "/verein/vorstand/benachrichtigungen", data={"csrf_token": csrf}
        ).status_code
        == 303
    )
    bodies = [item[2] for item in mail[sent_before:]]
    assert len(bodies) == 2
    assert "1. Juli 2025" in bodies[0]
    assert "1. Juni 2026" in bodies[1] and "1. Juli 2026" in bodies[1]


def test_period_from_another_member_cannot_receive_payment(membership):
    client, _mail, store, _reviewer_id, path = _accepted(membership)
    with client.session_transaction() as session:
        board = session["verein_session"]
    confirmation = store.apply(
        email="other-member@example.org",
        name="Other Member",
        affiliation="",
        locale="de_CH",
        privacy_version="current",
    )
    store.confirm(confirmation)
    identifier = str(store.list_applications(board)[0]["id"])
    store.decide(board, identifier, "accepted", "Checked independence")
    other_dues = store.application(board, identifier)["dues"][0]
    with pytest.raises(store.VereinDenied):
        store.record_payment(
            board,
            path.rsplit("/", 1)[1],
            dues_id=str(other_dues["id"]),
            amount_chf="50",
            source="bank-test",
            reference="wrong-member",
            evidence="Test statement",
        )


def test_termination_cancels_all_unsent_contribution_notices(membership, monkeypatch):
    import database
    from store import verein

    now = datetime(2025, 6, 1, 12, tzinfo=UTC)
    monkeypatch.setattr(verein, "now_utc", lambda: now)
    client, _mail, store, _reviewer_id, path = _accepted(membership)
    identifier = path.rsplit("/", 1)[1]
    with client.session_transaction() as session:
        board = session["verein_session"]
    now = datetime(2026, 8, 1, 12, tzinfo=UTC)
    store.renew_dues(board)
    dues = store.application(board, identifier)["dues"]
    for period in dues:
        store.request_reminder(board, identifier, dues_id=str(period["id"]))
    old = min(dues, key=lambda item: item["period_start"])
    with database.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE verein_dues SET reminder_sent_at='2026-01-01' WHERE id=%s",
            (str(old["id"]),),
        )
    stale = next(
        n for n in store.pending_notifications(board) if n["kind"] == "reminder"
    )
    store.terminate(
        board, identifier, "Unpaid after actual notice", dues_id=str(old["id"])
    )
    assert [n["kind"] for n in store.pending_notifications(board)] == ["terminated"]
    called = []
    assert (
        store.send_notification(
            board, str(stale["id"]), lambda notice: called.append(True)
        )
        is None
    )
    assert called == []
    store.record_delivery(board, str(stale["id"]), True)
    with database.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT sent_at,superseded_at FROM verein_notifications WHERE id=%s",
            (str(stale["id"]),),
        )
        record = cur.fetchone()
    assert record["sent_at"] is None and record["superseded_at"] is not None
    # Historical queued rows are also ineligible even without a cancellation.
    with database.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE verein_notifications SET superseded_at=NULL WHERE id=%s",
            (str(stale["id"]),),
        )
    assert [n["kind"] for n in store.pending_notifications(board)] == ["terminated"]
    assert (
        store.send_notification(
            board, str(stale["id"]), lambda notice: called.append(True)
        )
        is None
    )
    store.record_delivery(board, str(stale["id"]), True)
    assert called == []


def test_upgrade_applies_pending_resignation_at_original_receipt_once(membership):
    import database

    client, _mail, store, _reviewer_id, path = _accepted(membership)
    identifier = path.rsplit("/", 1)[1]
    received = datetime(2026, 11, 5, 12, tzinfo=UTC)
    with client.session_transaction() as session:
        board = session["verein_session"]
    with database.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "ALTER TABLE verein_member_requests DROP CONSTRAINT verein_member_requests_status_check"
        )
        cur.execute(
            "ALTER TABLE verein_member_requests ADD CONSTRAINT verein_member_requests_status_check CHECK(status IN ('pending','approved','dismissed'))"
        )
        cur.execute(
            "INSERT INTO verein_member_requests(id,application_id,kind,reason,created_at) VALUES (gen_random_uuid(),%s,'resignation','Original declaration',%s)",
            (identifier, received),
        )
        store.create_tables(cur)
        store.create_tables(cur)
        cur.execute(
            "SELECT status,ended_at,end_reviewer FROM verein_members WHERE application_id=%s",
            (identifier,),
        )
        member = cur.fetchone()
        cur.execute(
            "SELECT status,created_at,resolved_at,reason FROM verein_member_requests WHERE application_id=%s",
            (identifier,),
        )
        request = cur.fetchone()
    assert member == {"status": "resigned", "ended_at": received, "end_reviewer": None}
    assert request == {
        "status": "received",
        "created_at": received,
        "resolved_at": received,
        "reason": "Original declaration",
    }
    assert store.list_member_requests(board) == []
    assert [n["kind"] for n in store.pending_notifications(board)] == [
        "resignation_received"
    ]


def test_reminder_uses_receipt_balance_at_delivery_even_after_batch_fetch(
    membership, monkeypatch
):
    import database

    client, mail, store, _reviewer_id, path = _accepted(membership)
    identifier = path.rsplit("/", 1)[1]
    with client.session_transaction() as session:
        board = session["verein_session"]
    monkeypatch.setattr(store, "now_utc", lambda: datetime(2027, 2, 1, tzinfo=UTC))
    store.request_reminder(board, identifier)
    fetch = database.get_verein_notifications

    def fetch_then_receive(token):
        batch = fetch(token)
        store.record_payment(
            token,
            identifier,
            amount_chf="20",
            source="test-bank",
            reference="late-receipt",
            evidence="Verified test statement",
        )
        return batch

    monkeypatch.setattr(database, "get_verein_notifications", fetch_then_receive)
    response = client.post(
        "/verein/vorstand/benachrichtigungen",
        data={"csrf_token": _csrf(client, "/verein/vorstand/antraege")},
    )
    assert response.status_code == 303
    reminder = next(item[2] for item in mail if "offen" in item[2])
    assert "CHF 30.00 offen" in reminder
