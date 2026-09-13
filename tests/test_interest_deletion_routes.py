# SPDX-License-Identifier: AGPL-3.0-or-later
"""Both interest sources use the verified deletion HTTP journey."""

import re
import uuid
from unittest.mock import MagicMock

import pytest

import database as db
from tests import test_interest_confirmation_routes, test_interest_postgres

interest_database = test_interest_postgres.interest_database
interest_client = test_interest_confirmation_routes.interest_client


@pytest.mark.integration
@pytest.mark.parametrize("with_building", [False, True])
def test_coverage_unsubscribe_requires_confirmation_and_removes_recipient(
    interest_client, monkeypatch, with_building
):
    client, _tasks, _cluster = interest_client
    if with_building:
        assert test_interest_postgres.save_registration(verified=True)
        with db.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE consents SET updates_opt_in = TRUE WHERE building_id = %s",
                ("interest-building",),
            )
    for email in ("one@example.ch", "other@example.ch"):
        token = str(uuid.uuid4())
        assert db.save_coverage_request(
            request_id=str(uuid.uuid4()),
            email=email,
            address="Testweg 1",
            plz="4533",
            municipality_name="Riedholz",
            canton="SO",
            bfs_number=2554,
            roles=[],
            has_solar=False,
            verification_token=token,
        )
        assert db.verify_coverage_request(token)
    import app

    sent = MagicMock()
    monkeypatch.setattr(app, "send_email", sent)
    response = client.post("/unsubscribe", data={"email": "ONE@example.ch"})
    assert response.status_code == 200
    assert sent.call_count == (2 if with_building else 1)
    paths = [
        re.search(r"http://localhost:5003(/unsubscribe/[\w-]+)", call.args[2]).group(1)
        for call in sent.call_args_list
    ]
    for path in paths:
        assert client.get(path).status_code == 200
    assert db.get_verified_interest_recipients(2554, exclude_email="") == [
        "one@example.ch",
        "other@example.ch",
    ]
    for path in paths:
        assert client.post(path).status_code == 200
        assert client.post(path).status_code == 404
    assert db.get_verified_interest_recipients(2554, exclude_email="") == [
        "other@example.ch"
    ]
    assert [row["email"] for row in db.get_operator_interest_records()] == [
        "other@example.ch"
    ]


@pytest.mark.integration
@pytest.mark.parametrize("failure", ["expired", "storage"])
def test_failed_coverage_deletion_keeps_the_record(interest_client, failure):
    client, _tasks, _cluster = interest_client
    assert db.save_coverage_request(
        request_id="pending",
        email="one@example.ch",
        address="Testweg 1",
        plz="4533",
        municipality_name="Riedholz",
        canton="SO",
        bfs_number=2554,
        roles=[],
        has_solar=False,
        verification_token="confirm-pending",
    )
    (token,) = db.create_coverage_deletion_tokens("one@example.ch")
    with db.get_connection() as conn, conn.cursor() as cur:
        if failure == "expired":
            cur.execute("UPDATE tokens SET expires_at = NOW() - INTERVAL '1 second'")
        else:
            cur.execute("""CREATE FUNCTION reject_deletion() RETURNS trigger AS $$
                BEGIN RAISE EXCEPTION 'injected delete failure'; END;
                $$ LANGUAGE plpgsql""")
            cur.execute(
                "CREATE TRIGGER reject_deletion BEFORE DELETE ON coverage_requests "
                "FOR EACH ROW EXECUTE FUNCTION reject_deletion()"
            )
    assert client.post(f"/unsubscribe/{token}").status_code == (
        404 if failure == "expired" else 409
    )
    assert len(db.get_operator_interest_records()) == 1
    if failure == "storage":
        assert db.get_token(token) is not None


@pytest.mark.integration
def test_coverage_deletion_link_does_not_reach_a_later_submission(interest_client):
    client, _tasks, _cluster = interest_client
    for request_id in ("original", "later"):
        assert db.save_coverage_request(
            request_id=request_id,
            email="one@example.ch",
            address="Testweg 1",
            plz="4533",
            municipality_name="Riedholz",
            canton="SO",
            bfs_number=2554,
            roles=[],
            has_solar=False,
            verification_token=request_id,
        )
        if request_id == "original":
            (token,) = db.create_coverage_deletion_tokens("one@example.ch")
    assert client.post(f"/unsubscribe/{token}").status_code == 200
    assert len(db.get_operator_interest_records()) == 1
    assert db.verify_coverage_request("later") is not None
