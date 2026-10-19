# SPDX-License-Identifier: AGPL-3.0-or-later
"""Create a LEG through its form, authenticated route and PostgreSQL store."""

import os
import re
from unittest.mock import patch

import pytest

from tests import test_interest_postgres
from tests.test_dashboard_access_routes import _set_session

interest_database = test_interest_postgres.interest_database
pytestmark = pytest.mark.integration


@pytest.fixture
def founder(interest_database):
    assert interest_database.save_building(
        "founder",
        "founder@example.invalid",
        {"address": "Synthetic 1", "lat": 47.2, "lon": 8.2, "bfs_number": 261},
        {},
        verified=True,
    )
    with patch.dict(
        os.environ,
        {"DATABASE_URL": os.environ["DATABASE_URL"], "REDIS_URL": ""},
        clear=True,
    ):
        import app

        web = app.create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "synthetic-founder",
                "APP_BASE_URL": "http://localhost",
                "RATELIMIT_STORAGE_URI": "memory://",
            },
            load_environment=False,
            check_database=False,
        )
        yield web.test_client(), interest_database


@pytest.mark.parametrize("discovery_consent", [False, True])
def test_founder_can_submit_the_rendered_create_form(founder, discovery_consent):
    client, db = founder
    _set_session(client, "founder")
    if discovery_consent:
        with db.get_connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE consents SET share_with_neighbors = TRUE WHERE building_id = %s",
                ("founder",),
            )

    page = client.get("/leg/community/create")

    assert page.status_code == 200
    assert '<form method="post" action="/leg/community/create"' in page.text
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    response = client.post(
        "/leg/community/create",
        data={
            "csrf_token": csrf,
            "name": "LEG Synthetic",
            "distribution_model": "proportional",
            "building_id": "another-founder",
        },
    )

    assert response.status_code == 302
    community = db.fetch_user_communities("founder")[0]
    assert community["name"] == "LEG Synthetic"
    assert community["role"] == "admin"
    assert community["distribution_model"] == "proportional"
    assert response.location == "/leg/dashboard?cid=" + community["community_id"]
    assert db.fetch_user_communities("another-founder") == []
    members = db.fetch_community_with_members(community["community_id"], "founder")[
        "members"
    ]
    assert [member["building_id"] for member in members] == ["founder"]
    assert "LEG Synthetic" in client.get(response.location).text


def test_creation_error_keeps_the_form_available(founder):
    client, db = founder
    _set_session(client, "founder")

    response = client.post(
        "/leg/community/create",
        data={
            "csrf_token": "csrf-secret",
            "name": "",
            "distribution_model": "proportional",
        },
    )

    assert response.status_code == 400
    assert '<form method="post" action="/leg/community/create"' in response.text
    assert 'value="proportional" selected' in response.text
    assert db.fetch_user_communities("founder") == []


def test_create_form_requires_a_session_and_post_requires_csrf(founder):
    client, db = founder

    assert client.get("/leg/community/create").status_code == 401
    _set_session(client, "founder")
    response = client.post("/leg/community/create", data={"name": "LEG Synthetic"})

    assert response.status_code == 400
    assert db.fetch_user_communities("founder") == []


def test_private_founder_view_keeps_other_households_behind_current_consent(founder):
    client, db = founder
    _set_session(client, "founder")
    response = client.post(
        "/leg/community/create",
        data={
            "csrf_token": "csrf-secret",
            "name": "LEG Synthetic",
        },
    )
    cid = db.fetch_user_communities("founder")[0]["community_id"]
    for bid, consent in (("visible", True), ("private", False), ("outsider", True)):
        assert db.save_building(
            bid,
            f"{bid}@example.invalid",
            {"address": f"Synthetic {bid}", "lat": 47.2, "lon": 8.2, "bfs_number": 261},
            {"share_with_neighbors": consent},
            verified=True,
        )
        if bid != "outsider":
            assert db.insert_invited_member(cid, bid, "founder")

    row = db.fetch_community_with_members(cid, "founder")
    assert {m["building_id"] for m in row["members"]} == {"founder", "visible"}
    assert db.fetch_community_with_members(cid, "outsider") is None
    legacy = db.fetch_community_with_members(cid)
    assert {m["building_id"] for m in legacy["members"]} == {"visible"}
    with db.get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            "UPDATE consents SET share_with_neighbors = FALSE WHERE building_id = %s",
            ("visible",),
        )
    assert {
        m["building_id"]
        for m in db.fetch_community_with_members(cid, "founder")["members"]
    } == {"founder"}
    page = client.get(response.location)
    assert "LEG Synthetic" in page.text
    assert "private@example.invalid" not in page.text
    assert "visible@example.invalid" not in page.text
    _set_session(client, "outsider")
    assert "LEG Synthetic" not in client.get(response.location).text


def test_founder_without_discovery_consent_can_generate_documents_and_manage_api(
    founder,
):
    import weasyprint

    client, db = founder
    _set_session(client, "founder")
    assert (
        client.post(
            "/leg/community/create",
            data={"csrf_token": "csrf-secret", "name": "Synthetic founder LEG"},
        ).status_code
        == 302
    )
    cid = db.fetch_user_communities("founder")[0]["community_id"]
    assert db.save_building(
        "partner",
        "partner@example.invalid",
        {
            "address": "Synthetic partner",
            "lat": 47.2,
            "lon": 8.2,
            "potential_pv_kwp": 10,
        },
        {"share_with_neighbors": True},
        verified=True,
    )
    assert db.insert_invited_member(cid, "partner", "founder")
    assert db.confirm_invited_member(cid, "partner")
    assert db.save_building(
        "partner-two",
        "partner-two@example.invalid",
        {"address": "Synthetic second partner", "lat": 47.2, "lon": 8.2},
        {"share_with_neighbors": True},
        verified=True,
    )
    assert db.insert_invited_member(cid, "partner-two", "founder")
    assert db.confirm_invited_member(cid, "partner-two")
    rendered = []
    original_html = weasyprint.HTML

    def capture_html(*args, **kwargs):
        rendered.append(kwargs["string"])
        return original_html(*args, **kwargs)

    with patch("weasyprint.HTML", side_effect=capture_html):
        response = client.post(
            f"/leg/community/{cid}/documents", data={"csrf_token": "csrf-secret"}
        )
    assert response.status_code == 302
    documents = db.list_leg_documents(cid)
    assert {doc["doc_type"] for doc in documents} == {
        "gemeinschaftsvereinbarung",
        "teilnehmervertrag",
    }
    assert len(documents) == 3
    assert not any(
        "founder@example.invalid" in document["filename"] for document in documents
    )
    assert all(
        bytes(db.get_leg_document(doc["id"])["pdf_data"]).startswith(b"%PDF")
        for doc in documents
    )
    assert "founder@example.invalid" not in "\n".join(rendered)
    assert "Synthetic 1" not in "\n".join(rendered)
    assert "partner@example.invalid" in "\n".join(rendered)
    assert "partner-two@example.invalid" in "\n".join(rendered)
    response = client.post(
        f"/leg/community/{cid}/operator-api/credentials",
        data={
            "csrf_token": "csrf-secret",
            "name": "Synthetic client",
            "capabilities": "formation.read",
        },
    )
    assert response.status_code == 201
    _set_session(client, "partner")
    for document in documents:
        download = client.get(f"/leg/document/{document['id']}")
        assert download.status_code == 200
        assert download.data == bytes(db.get_leg_document(document["id"])["pdf_data"])
