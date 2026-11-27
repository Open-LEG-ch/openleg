# SPDX-License-Identifier: AGPL-3.0-or-later
"""Registry publication through HTTP and native PostgreSQL."""

import re

import pytest

from tests import test_verein_membership

isolated_environment = test_verein_membership.isolated_environment
app_module = test_verein_membership.app_module
web = test_verein_membership.web
membership = test_verein_membership.membership
pytestmark = pytest.mark.integration

ADMIN_HEADERS = {"X-Admin-Token": "registry-test-admin"}
CONTACT = "private-registry-contact@example.ch"
NAME = "Fictional Solar LEG"


@pytest.fixture
def registry(membership, monkeypatch):
    import database as db

    client, _mail, _store = membership
    monkeypatch.setenv("ADMIN_TOKEN", ADMIN_HEADERS["X-Admin-Token"])
    db.create_tables()
    assert db.save_municipality_profile(
        {"bfs_number": 4021, "name": "Baden", "kanton": "AG"}
    )
    return client, db


def submit(client, db, **location):
    response = client.post(
        "/api/registry/publish",
        json={"name": NAME, "contact_email": CONTACT, **location},
    )
    assert response.status_code == 201
    result = response.get_json()
    assert result["moderation_status"] == "pending"
    row = db.get_registry_entry_by_slug(result["slug"])
    assert row["bfs_number"] is None
    return row


def approval_path(row):
    return f"/admin/registry/{row['id']}/approve"


def moderation_page(client):
    response = client.get(
        "/admin/ops", headers={**ADMIN_HEADERS, "Accept": "text/html"}
    )
    assert response.status_code == 200
    return response.text


def approval_form(client, row):
    match = re.search(
        rf'<form[^>]*action="{approval_path(row)}"[^>]*>(.*?)</form>',
        moderation_page(client),
        re.DOTALL,
    )
    assert match is not None
    return match[1]


def test_unique_match_requires_confirmation_and_publishes_to_municipality(registry):
    client, db = registry
    entry = submit(client, db, ort="Baden", plz="5400", kanton="AG")
    path = approval_path(entry)

    assert client.post(path, headers=ADMIN_HEADERS).status_code == 422
    assert client.get("/api/v1/registry/" + entry["slug"]).status_code == 404

    form = approval_form(client, entry)
    assert re.findall(r'<option value="([0-9]+)"', form) == ["4021"]
    assert "selected" not in form
    assert (
        client.application.test_client()
        .post(path, data={"municipality": "4021"})
        .status_code
        == 403
    )
    assert client.post(path, data={"municipality": "4021"}).status_code == 403
    with client.session_transaction() as session:
        csrf = session["admin_csrf"]
    response = client.post(path, data={"csrf_token": csrf, "municipality": "4021"})
    assert response.status_code == 302
    published = db.get_registry_entry(entry["id"])
    assert published["moderation_status"] == "published"
    assert published["bfs_number"] == 4021
    for url in ["/gemeinde/profil/4021", "/leg-check?q=Baden&bfs=4021"]:
        public = client.get(url)
        assert public.status_code == 200
        assert NAME in public.text


def test_shared_postcode_and_locality_require_a_choice(registry):
    client, db = registry
    for bfs, name in [(13, "Stallikon"), (261, "Zürich")]:
        assert db.save_municipality_profile(
            {"bfs_number": bfs, "name": name, "kanton": "ZH"}
        )
    entry = submit(client, db, ort="Uetliberg", plz="8143", kanton="ZH")

    form = approval_form(client, entry)
    assert re.findall(r'<option value="([0-9]+)"', form) == ["13", "261"]
    assert "selected" not in form
    path = approval_path(entry)
    assert client.post(path, headers=ADMIN_HEADERS).status_code == 422
    assert (
        client.post(
            path, headers=ADMIN_HEADERS, data={"municipality": ["13", "261"]}
        ).status_code
        == 422
    )
    assert db.get_registry_entry(entry["id"])["moderation_status"] == "pending"
    assert (
        client.post(
            path, headers=ADMIN_HEADERS, data={"municipality": "261"}
        ).status_code
        == 302
    )
    assert db.get_registry_entry(entry["id"])["bfs_number"] == 261
    assert NAME in client.get("/leg-check?q=Zürich&bfs=261").text
    assert NAME not in client.get("/leg-check?q=Stallikon&bfs=13").text


def test_unmatched_entry_can_be_published_with_an_explicit_no_municipality(registry):
    client, db = registry
    entry = submit(client, db, ort="Unbekannter Ort", plz="5400", kanton="AG")
    path = approval_path(entry)
    form = approval_form(client, entry)
    assert re.findall(r'<option value="([0-9]+)"', form) == []
    assert "Keine passende Gemeinde gefunden." in form
    assert '<option value="none">' in form

    for data in ({}, {"municipality": "9876"}, {"manual_bfs_number": "none"}):
        assert client.post(path, headers=ADMIN_HEADERS, data=data).status_code == 422
    assert db.get_registry_entry(entry["id"])["moderation_status"] == "pending"
    assert (
        client.post(
            path, headers=ADMIN_HEADERS, data={"municipality": "none"}
        ).status_code
        == 302
    )
    published = db.get_registry_entry(entry["id"])
    assert published["moderation_status"] == "published"
    assert published["bfs_number"] is None
    assert published["without_municipality"] is True
    assert path not in moderation_page(client)
    assert (
        client.post(
            path, headers=ADMIN_HEADERS, data={"municipality": "4021"}
        ).status_code
        == 422
    )
    assert client.get("/leg-verzeichnis/" + entry["slug"]).status_code == 200
    assert NAME not in client.get("/leg-check?q=Baden&bfs=4021").text


def test_unmatched_entry_can_use_a_moderator_supplied_bfs_number(registry):
    client, db = registry
    entry = submit(client, db, ort="Unbekannter Ort", kanton="AG")
    assert 'name="manual_bfs_number"' in approval_form(client, entry)
    response = client.post(
        approval_path(entry), headers=ADMIN_HEADERS, data={"manual_bfs_number": "4021"}
    )
    assert response.status_code == 302
    assert db.get_registry_entry(entry["id"])["bfs_number"] == 4021
    assert NAME in client.get("/leg-check?q=Baden&bfs=4021").text


def test_legacy_publication_can_be_repaired_without_exposing_private_data(registry):
    client, db = registry
    slug = "legacy-solar-leg"
    markers = (
        CONTACT,
        "private-claim-token",
        "private-verification-token",
        "private-note",
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("ALTER TABLE leg_registry DROP COLUMN without_municipality")
        cur.execute(
            """
            INSERT INTO leg_registry (
                slug, name, kanton, plz, ort, moderation_status,
                contact_email, claim_token, verification_token, moderation_note
            ) VALUES (%s, %s, 'AG', '5405', 'Dättwil', 'published', %s, %s, %s, %s)
            RETURNING id
            """,
            (slug, NAME, *markers),
        )
        entry = cur.fetchone()
    db.create_tables()

    assert NAME not in client.get("/leg-check?q=Baden&bfs=4021").text
    assert "Veröffentlicht, Gemeinde fehlt" in moderation_page(client)
    form = approval_form(client, entry)
    assert re.findall(r'<option value="([0-9]+)"', form) == ["4021"]
    assert (
        client.post(
            approval_path(entry), headers=ADMIN_HEADERS, data={"municipality": "4021"}
        ).status_code
        == 302
    )
    assert approval_path(entry) not in moderation_page(client)
    repaired = db.get_registry_entry(entry["id"])
    assert repaired["bfs_number"] == 4021
    assert repaired["moderation_note"] == "private-note"
    urls = [
        "/leg-check?q=Baden&bfs=4021",
        "/leg-verzeichnis?bfs=4021",
        f"/leg-verzeichnis/{slug}",
        "/api/v1/registry",
        f"/api/v1/registry/{slug}",
        "/gemeinde/profil/4021",
    ]
    for url in urls:
        result = client.get(url)
        assert result.status_code == 200
        assert all(marker not in result.text for marker in markers), url
        if url != "/gemeinde/profil/4021":
            assert NAME in result.text, url
