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
    assert ('aria-label="LEG-Verwaltung"' in page.text) is (status == "confirmed")
    for view, private_text in (
        ("members", "Privateweg 1"),
        ("correspondence", "Private subject"),
    ):
        assert status == "confirmed" or private_text not in page.text
        response = client.get(f"/leg/community/{cid}/{view}")
        assert response.status_code == (200 if status == "confirmed" else 403)
        assert (private_text in response.text) is (status == "confirmed")
    for path in (
        f"/leg/document/{doc}",
        f"/leg/community/{cid}/correspondence/{entry}/attachment",
    ):
        response = client.get(path)
        assert response.status_code == (200 if status == "confirmed" else 404)
        assert (b"%PDF-private-record" in response.data) is (status == "confirmed")


def test_invitee_can_accept_without_access_to_private_community_records(community):
    client, db, cid, _, _ = community
    members = f"/leg/community/{cid}/members"
    assert client.get(members).status_code == 403
    page = client.get(f"/leg/dashboard?cid={cid}")
    assert page.status_code == 200
    assert "Einladung zur LEG" in page.text
    assert "Teilnahme bestätigen" in page.text
    assert f'action="/leg/community/{cid}/confirm"' in page.text
    assert 'name="csrf_token" value="csrf-secret"' in page.text
    assert "Privateweg 1" not in page.text
    assert "Private subject" not in page.text
    response = client.post(
        f"/leg/community/{cid}/confirm", data={"csrf_token": "csrf-secret"}
    )
    assert response.status_code == 302
    assert response.location == f"/leg/dashboard?cid={cid}"
    member = next(
        row
        for row in db.fetch_community_with_members(cid, "guest")["members"]
        if row["building_id"] == "guest"
    )
    assert member["status"] == "confirmed"
    assert f'href="{members}"' in client.get(response.location).text
    assert "Privateweg 1" in client.get(members).text


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


@pytest.mark.parametrize("member_status", ["invited", "rejected"])
@pytest.mark.parametrize("leg_status", ["interested", "active"])
def test_unconfirmed_member_formation_redirect_hides_leg_status(
    community, member_status, leg_status
):
    client, db, cid, _, _ = community
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE communities SET status=%s WHERE community_id=%s", (leg_status, cid)
        )
        cur.execute(
            "UPDATE community_members SET status=%s WHERE community_id=%s AND building_id='guest'",
            (member_status, cid),
        )

    root = f"/leg/community/{cid}"
    response = client.post(
        f"{root}/start-formation", data={"csrf_token": "csrf-secret"}
    )
    assert response.status_code == 302
    assert response.location == f"/leg/dashboard?cid={cid}"

    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE community_members SET status='confirmed' WHERE community_id=%s AND building_id='guest'",
            (cid,),
        )
    message = "Nur die Administration kann die Gründung starten."
    for page in (response.location, f"{root}/formation"):
        assert message not in client.get(page).text


def test_journal_lists_attachment_metadata_without_pdf_bytes(community):
    _client, db, cid, _doc, entry = community
    row = db.list_correspondence(cid)[0]
    assert row["attachment_filename"] == "private.pdf"
    assert "attachment_data" not in row
    assert bytes(db.get_correspondence_attachment(entry, cid)["attachment_data"]) == (
        b"%PDF-private-record"
    )
