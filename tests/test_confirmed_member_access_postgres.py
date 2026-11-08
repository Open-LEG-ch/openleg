# SPDX-License-Identifier: AGPL-3.0-or-later
"""An invitation is not permission to read another household's LEG records."""

import pytest

from tests import security_review_fixtures
from tests import test_interest_postgres as interest_postgres_tests
from tests.test_dashboard_access_routes import _set_session

app_module = security_review_fixtures.app_module
interest_database = interest_postgres_tests.interest_database

pytestmark = pytest.mark.integration


@pytest.fixture
def community(app_module, interest_database):
    db = interest_database
    for bid in ("owner", "guest"):
        assert db.save_building(
            bid,
            f"{bid}@example.ch",
            {"address": "Privateweg 1", "lat": 47.2, "lon": 8.2},
            {"share_with_neighbors": True},
        )
    cid = db.create_community_record("Private community", "owner", "simple", "")
    assert db.insert_invited_member(cid, "guest", "owner")
    doc = db.store_leg_document(cid, "agreement", b"%PDF-private-record", "private.pdf")
    entry = db.log_correspondence(
        cid,
        "in",
        "email",
        "Private counterparty",
        "Private subject",
        attachment_filename="private.pdf",
        attachment_mime="application/pdf",
        attachment_data=b"%PDF-private-record",
    )
    client = app_module.web.test_client()
    _set_session(client, "guest")
    return client, db, cid, doc, entry


@pytest.mark.parametrize("status", ["invited", "rejected", "confirmed"])
def test_only_confirmed_members_can_read_private_pages_and_files(community, status):
    client, db, cid, doc, entry = community
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE community_members SET status=%s WHERE community_id=%s AND building_id='guest'",
            (status, cid),
        )
    page = client.get(f"/leg/dashboard?cid={cid}")
    assert page.status_code == 200
    assert ("Privateweg 1" in page.text) is (status == "confirmed")
    assert ("Private subject" in page.text) is (status == "confirmed")
    for path in (
        f"/leg/document/{doc}",
        f"/leg/community/{cid}/correspondence/{entry}/attachment",
    ):
        response = client.get(path)
        assert response.status_code == (200 if status == "confirmed" else 404)
        assert (b"%PDF-private-record" in response.data) is (status == "confirmed")


def test_invitee_can_accept_without_access_to_private_community_records(community):
    client, _db, cid, _, _ = community
    page = client.get(f"/leg/dashboard?cid={cid}")
    assert f'action="/leg/community/{cid}/confirm"' in page.text
    assert "Privateweg 1" not in page.text
    response = client.post(
        f"/leg/community/{cid}/confirm", data={"csrf_token": "csrf-secret"}
    )
    assert response.status_code == 302
    assert "Privateweg 1" in client.get(response.location).text


def test_invitee_cannot_append_to_private_correspondence(community):
    client, db, cid, _, _ = community
    before = db.list_correspondence(cid)
    response = client.post(
        f"/leg/community/{cid}/correspondence",
        data={
            "csrf_token": "csrf-secret",
            "direction": "out",
            "channel": "email",
            "subject": "Unauthorized",
        },
    )
    assert response.status_code == 400
    assert db.list_correspondence(cid) == before
