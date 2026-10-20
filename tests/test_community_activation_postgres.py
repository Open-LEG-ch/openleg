# SPDX-License-Identifier: AGPL-3.0-or-later
"""Record actual VNB start evidence before explicitly activating a LEG."""

import hashlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from tests import test_community_create_postgres
from tests.test_dashboard_access_routes import _set_session

interest_database = test_community_create_postgres.interest_database
founder = test_community_create_postgres.founder
pytestmark = pytest.mark.integration


@pytest.fixture
def submitted(founder):
    client, db = founder
    _set_session(client, "founder")
    cid = db.create_community_record("Synthetic LEG", "founder", "simple", "")
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE communities SET status = 'dso_submitted' WHERE community_id = %s",
            (cid,),
        )
    entry = db.log_correspondence(
        cid,
        "in",
        "email",
        "Synthetic VNB",
        "Synthetic start confirmation",
        logged_by="founder",
        attachment_filename="synthetic.txt",
        attachment_mime="text/plain",
        attachment_data=b"Synthetic approval evidence",
    )
    fields = {
        "csrf_token": "csrf-secret",
        "correspondence_id": str(entry),
        "effective_date": datetime.now(ZoneInfo("Europe/Zurich")).date().isoformat(),
        "reference": "SYNTHETIC-START-1",
        "confirm_start": "yes",
    }
    return client, db, cid, fields


def test_french_activation_labels_errors_and_confirmation_remain_localized(
    submitted, monkeypatch
):
    client, db, cid, fields = submitted
    # This fixture already initialized its isolated PostgreSQL schema.
    monkeypatch.setattr(db, "_db_initialized", True)
    page = client.get(f"/leg/dashboard?cid={cid}&lang=fr-CH")
    assert page.status_code == 200
    assert "Mise en service" in page.text
    assert "confirme la mise en service de la CEL à cette date." in page.text
    assert "Référence du gestionnaire de réseau" in page.text
    assert "Betriebsstart" not in page.text

    invalid = client.post(
        f"/leg/community/{cid}/vnb-start-confirmation",
        data={**fields, "reference": ""},
    )
    assert invalid.status_code == 400
    assert "Saisissez une référence de 255 caractères au maximum." in invalid.text
    assert "Geben Sie eine Referenz" not in invalid.text
    confirmed = client.post(f"/leg/community/{cid}/vnb-start-confirmation", data=fields)
    assert confirmed.status_code == 302
    after = client.get(confirmed.location)
    assert after.status_code == 200
    assert "Mise en service confirmée" in after.text
    assert "Activer la CEL" in after.text
    assert fields["reference"] in after.text


def test_record_start_confirmation_then_explicitly_activate(founder):
    client, db = founder
    _set_session(client, "founder")
    cid = db.create_community_record("Synthetic LEG", "founder", "simple", "")
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE communities SET status = 'dso_submitted' WHERE community_id = %s",
            (cid,),
        )
    evidence = b"Synthetic VNB start confirmation, not a real approval"
    entry = db.log_correspondence(
        cid,
        "in",
        "email",
        "Synthetic VNB",
        "Start confirmation",
        logged_by="founder",
        attachment_filename="confirmation.txt",
        attachment_mime="text/plain",
        attachment_data=evidence,
    )
    today = datetime.now(ZoneInfo("Europe/Zurich")).date().isoformat()
    fields = {
        "csrf_token": "csrf-secret",
        "correspondence_id": str(entry),
        "effective_date": today,
        "reference": "SYNTHETIC-START-1",
        "confirm_start": "yes",
    }

    response = client.post(f"/leg/community/{cid}/vnb-start-confirmation", data=fields)

    assert response.status_code == 302
    row = db.fetch_community_with_members(cid, "founder")
    assert row["status"] == "dso_approved"
    assert row["activated_at"] is None
    assert row["vnb_start_confirmation"]["effective_date"] == today
    assert row["vnb_start_confirmation"]["correspondence_id"] == entry
    assert (
        row["vnb_start_confirmation"]["evidence_sha256"]
        == hashlib.sha256(evidence).hexdigest()
    )
    assert row["vnb_start_confirmation"]["recorded_by"] == "founder"
    approved_at = row["dso_approved_at"]
    response = client.post(
        f"/leg/community/{cid}/activate", data={"csrf_token": "csrf-secret"}
    )
    assert response.status_code == 302
    row = db.fetch_community_with_members(cid, "founder")
    assert row["status"] == "active"
    assert row["activated_at"] is not None
    assert row["vnb_start_confirmation"]["activated_by"] == "founder"
    assert row["dso_approved_at"] == approved_at
    activated_at = row["activated_at"]
    assert (
        client.post(
            f"/leg/community/{cid}/activate", data={"csrf_token": "csrf-secret"}
        ).status_code
        == 302
    )
    assert (
        db.fetch_community_with_members(cid, "founder")["activated_at"] == activated_at
    )
    assert "SYNTHETIC-START-1" in client.get(response.location).text


@pytest.mark.parametrize(
    "state", ["interested", "signatures_pending", "dso_submitted", "dso_approved"]
)
def test_activation_requires_recorded_start_evidence(submitted, state):
    client, db, cid, fields = submitted
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE communities SET status = %s WHERE community_id = %s", (state, cid)
        )
    response = client.post(f"/leg/community/{cid}/activate", data=fields)
    assert response.status_code == 409
    assert db.fetch_community_with_members(cid, "founder")["status"] == state


def test_confirmation_requires_a_submitted_community(submitted):
    client, db, cid, fields = submitted
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE communities SET status = 'interested' WHERE community_id = %s",
            (cid,),
        )
    assert (
        client.post(
            f"/leg/community/{cid}/vnb-start-confirmation", data=fields
        ).status_code
        == 409
    )
    assert (
        db.fetch_community_with_members(cid, "founder")["vnb_start_confirmation"]
        is None
    )


def test_future_confirmation_replay_and_changed_source_fail_closed(submitted):
    client, db, cid, fields = submitted
    fields["effective_date"] = (
        datetime.now(ZoneInfo("Europe/Zurich")).date() + timedelta(days=2)
    ).isoformat()
    url = f"/leg/community/{cid}/vnb-start-confirmation"
    assert client.post(url, data=fields).status_code == 302
    before = db.fetch_community_with_members(cid, "founder")
    assert client.post(url, data=fields).status_code == 302
    assert (
        db.fetch_community_with_members(cid, "founder")["dso_approved_at"]
        == before["dso_approved_at"]
    )
    assert client.post(url, data={**fields, "reference": "CHANGED"}).status_code == 409
    response = client.post(f"/leg/community/{cid}/activate", data=fields)
    assert response.status_code == 409
    assert "Zukunft" in response.text
    assert db.fetch_community_with_members(cid, "founder")["activated_at"] is None
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE correspondence_log SET attachment_data = %s WHERE id = %s",
            (b"Changed source", int(fields["correspondence_id"])),
        )
    assert client.post(url, data=fields).status_code == 409


@pytest.mark.parametrize(
    "role,status",
    [
        ("member", "confirmed"),
        ("admin", "invited"),
        ("documents", "confirmed"),
        ("outsider", "confirmed"),
    ],
)
def test_both_transitions_require_the_current_confirmed_admin(submitted, role, status):
    client, db, cid, fields = submitted
    assert (
        client.post(
            f"/leg/community/{cid}/vnb-start-confirmation", data=fields
        ).status_code
        == 302
    )
    assert db.save_building(
        "other",
        "other@example.invalid",
        {"address": "Synthetic 2", "lat": 47.2, "lon": 8.2},
        {},
        verified=True,
    )
    if role == "outsider":
        db.create_community_record("Other LEG", "other", "simple", "")
    else:
        assert db.insert_invited_member(cid, "other", "founder")
        with db.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE community_members SET role = %s, status = %s, access_roles = %s::jsonb WHERE community_id = %s AND building_id = 'other'",
                (
                    "admin" if role == "admin" else "member",
                    status,
                    '["documents"]' if role == "documents" else "[]",
                    cid,
                ),
            )
    _set_session(client, "other")
    for suffix in ("vnb-start-confirmation", "activate"):
        response = client.post(
            f"/leg/community/{cid}/{suffix}", data={**fields, "building_id": "founder"}
        )
        assert response.status_code == 403
        assert response.headers.get("Cache-Control") == "no-store"
        assert response.headers.get("Referrer-Policy") == "no-referrer"
    assert db.fetch_community_with_members(cid, "founder")["status"] == "dso_approved"


def test_transitions_require_session_csrf_and_explicit_attestation(submitted):
    client, db, cid, fields = submitted
    for suffix in ("vnb-start-confirmation", "activate"):
        assert (
            client.post(
                f"/leg/community/{cid}/{suffix}", data={**fields, "csrf_token": "bad"}
            ).status_code
            == 400
        )
    assert (
        client.post(
            f"/leg/community/{cid}/vnb-start-confirmation",
            data={**fields, "confirm_start": "no"},
        ).status_code
        == 400
    )
    with client.session_transaction() as session:
        session.clear()
    for suffix in ("vnb-start-confirmation", "activate"):
        assert (
            client.post(f"/leg/community/{cid}/{suffix}", data=fields).status_code
            == 401
        )
    assert (
        db.fetch_community_with_members(cid, "founder")["vnb_start_confirmation"]
        is None
    )


@pytest.mark.parametrize("source", ["other_community", "outgoing", "empty", "missing"])
def test_only_a_same_community_incoming_attachment_can_confirm_start(submitted, source):
    client, db, cid, fields = submitted
    source_cid = (
        db.create_community_record("Other LEG", "founder", "simple", "")
        if source == "other_community"
        else cid
    )
    entry = db.log_correspondence(
        source_cid,
        "out" if source == "outgoing" else "in",
        "email",
        "Synthetic VNB",
        "Synthetic source",
        attachment_filename="source.txt",
        attachment_data=b"" if source == "empty" else b"Synthetic source",
    )
    fields["correspondence_id"] = str(entry + 1000 if source == "missing" else entry)
    response = client.post(f"/leg/community/{cid}/vnb-start-confirmation", data=fields)
    assert response.status_code == 409
    assert db.fetch_community_with_members(cid, "founder")["status"] == "dso_submitted"


@pytest.mark.parametrize(
    "field,value",
    [
        ("effective_date", "2026-02-30"),
        ("effective_date", "20261019"),
        ("reference", ""),
        ("correspondence_id", "nonsense"),
    ],
)
def test_invalid_confirmation_does_not_change_community(submitted, field, value):
    client, db, cid, fields = submitted
    response = client.post(
        f"/leg/community/{cid}/vnb-start-confirmation", data={**fields, field: value}
    )
    assert response.status_code == 400
    assert db.fetch_community_with_members(cid, "founder")["status"] == "dso_submitted"


def test_changed_confirmation_attachment_blocks_activation(submitted):
    client, db, cid, fields = submitted
    assert (
        client.post(
            f"/leg/community/{cid}/vnb-start-confirmation", data=fields
        ).status_code
        == 302
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE correspondence_log SET attachment_data = %s WHERE id = %s",
            (b"Changed source", int(fields["correspondence_id"])),
        )
    assert client.post(f"/leg/community/{cid}/activate", data=fields).status_code == 409
    assert db.fetch_community_with_members(cid, "founder")["activated_at"] is None


def test_audit_timestamps_use_utc_with_a_non_utc_database_session(submitted):
    client, db, cid, fields = submitted
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SET TIME ZONE 'Pacific/Auckland'")
    assert (
        client.post(
            f"/leg/community/{cid}/vnb-start-confirmation", data=fields
        ).status_code
        == 302
    )
    assert client.post(f"/leg/community/{cid}/activate", data=fields).status_code == 302
    proof = db.fetch_community_with_members(cid, "founder")["vnb_start_confirmation"]
    assert proof["recorded_at"].endswith("+00:00")
    assert proof["activated_at"].endswith("+00:00")


def test_start_confirmation_and_original_attachment_survive_archive_restore(submitted):
    import community_archive

    client, db, cid, fields = submitted
    assert (
        client.post(
            f"/leg/community/{cid}/vnb-start-confirmation", data=fields
        ).status_code
        == 302
    )
    assert client.post(f"/leg/community/{cid}/activate", data=fields).status_code == 302
    before = db.fetch_community_with_members(cid, "founder")
    payload = community_archive.export_community_archive(cid)
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE communities, buildings, correspondence_log CASCADE")
    assert db.fetch_user_communities("founder") == []

    restored = community_archive.restore_community_archive(payload)

    assert restored["valid"], restored
    after = db.fetch_community_with_members(cid, "founder")
    assert after["status"] == "active"
    assert after["vnb_start_confirmation"] == before["vnb_start_confirmation"]
    assert after["dso_approved_at"] == before["dso_approved_at"]
    assert after["activated_at"] == before["activated_at"]
    assert client.post(f"/leg/community/{cid}/activate", data=fields).status_code == 302
    assert "SYNTHETIC-START-1" in client.get(f"/leg/dashboard?cid={cid}").text
