# SPDX-License-Identifier: AGPL-3.0-or-later
"""Published directory discovery through HTTP and the native storage seam."""

import html
import re
from urllib.parse import parse_qs, urlsplit

import pytest

from tests import test_verein_membership

isolated_environment = test_verein_membership.isolated_environment
app_module = test_verein_membership.app_module
web = test_verein_membership.web
membership = test_verein_membership.membership
pytestmark = pytest.mark.integration


@pytest.fixture
def directory(membership):
    import database

    client, _mail, _store = membership
    with client.application.app_context():
        database.create_tables()
    with database.get_connection() as conn, conn.cursor() as cur:
        for number in range(28):
            cur.execute(
                """INSERT INTO leg_registry(slug,name,kanton,plz,ort,bfs_number,
                moderation_status,contact_email,claim_token,verification_token,
                moderation_note,website_url,source,claimed_at,last_verified_at)
                VALUES (%s,%s,'AG','5400','Fictional Baden',4021,%s,
                'private-contact@example.invalid','private-claim-secret',
                'private-verify-secret','private-moderation-note','javascript:alert(1)',
                'claimed','2026-10-01','2026-11-01')""",
                (
                    f"fictional-{number:02}",
                    f"Fictional LEG {number:02}",
                    "published"
                    if number < 26
                    else "pending"
                    if number == 26
                    else "rejected",
                ),
            )
    return client, database


def _href(response, attribute):
    return html.unescape(re.search(attribute + r'="([^"]+)"', response.text)[1])


@pytest.mark.parametrize("language", ["de-CH", "en", "fr-CH", "it-CH"])
def test_published_results_preserve_search_and_identity_without_private_fields(
    directory, language
):
    client, db = directory
    query = {
        "q": "Fictional",
        "bfs": "4021",
        "kanton": "AG",
        "plz": "5400",
        "lang": language,
        "moderation_status": "pending",
    }
    result = client.get("/leg-verzeichnis", query_string=query)
    assert result.status_code == 200
    assert result.headers["Content-Language"] == language
    assert result.text.count("data-directory-entry=") == 25
    assert "fictional-26" not in result.text and "fictional-27" not in result.text
    for marker in [
        "private-contact",
        "private-claim",
        "private-verify",
        "private-moderation",
        "javascript:",
    ]:
        assert marker not in result.text
    detail_url = _href(result, "data-directory-entry")
    assert parse_qs(urlsplit(detail_url).query)["bfs"] == ["4021"]
    detail = client.get(detail_url)
    assert detail.status_code == 200
    for marker in [
        "private-contact",
        "private-claim",
        "private-verify",
        "private-moderation",
        "javascript:",
    ]:
        assert marker not in detail.text
    assert 'data-testid="registry-trust"' in detail.text
    back = _href(detail, "data-directory-back")
    assert parse_qs(urlsplit(back).query)["q"] == ["Fictional"]
    assert "/beanspruchen" not in detail.text and "/eintragen" not in result.text
    next_page = client.get(_href(result, "data-directory-next"))
    assert next_page.status_code == 200
    assert next_page.text.count("data-directory-entry=") == 1
    entries = db.read_public_registry(filters={"bfs_number": 4021})["entries"]
    assert entries and all(
        "contact_email" not in row and "claim_token" not in row for row in entries
    )


def test_unpublished_missing_empty_and_unavailable_are_distinct(directory):
    client, db = directory
    for slug in ["fictional-26", "fictional-27", "missing"]:
        assert client.get("/leg-verzeichnis/" + slug).status_code == 404
    empty = client.get("/leg-verzeichnis?q=No-such-LEG")
    assert empty.status_code == 200 and "Keine LEGs gefunden" in empty.text
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("DROP TABLE leg_registry")
    unavailable = client.get("/leg-verzeichnis")
    assert unavailable.status_code == 503
    assert "Keine LEGs gefunden" not in unavailable.text
    assert client.get("/leg-verzeichnis/fictional-00").status_code == 503


@pytest.mark.parametrize(
    "bad", [{"page": "-1"}, {"bfs": "4021 OR TRUE"}, {"kanton": "ZZ"}]
)
def test_invalid_search_keeps_valid_values_and_never_selects_unpublished(
    directory, bad
):
    client, _db = directory
    result = client.get("/leg-verzeichnis", query_string={"q": "Fictional", **bad})
    assert result.status_code == 400
    assert 'value="Fictional"' in result.text and 'role="alert"' in result.text
    assert "data-directory-entry=" not in result.text


def test_directory_stays_behind_the_complete_site_gate(web):
    client = web.test_client()
    assert client.get("/leg-verzeichnis").status_code == 404
    assert client.get("/leg-verzeichnis/fictional").status_code == 404


def test_sparse_public_record_escapes_content_and_labels_missing_dates(directory):
    client, db = directory
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO leg_registry(slug,name,contact_email,moderation_status,created_at,updated_at)
            VALUES ('sparse','Fictional <script>alert(1)</script>','private@example.invalid','published',NULL,NULL)""")
    response = client.get("/leg-verzeichnis/sparse")
    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    assert "nicht angegeben" in response.text
    assert '<p class="text-ink-muted">None' not in response.text


def test_malformed_stored_website_is_absent_without_breaking_results(directory):
    client, db = directory
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE leg_registry SET website_url='https://[' WHERE slug='fictional-00'"
        )
    assert client.get("/leg-verzeichnis?q=Fictional+LEG+00").status_code == 200
    detail = client.get("/leg-verzeichnis/fictional-00")
    assert detail.status_code == 200 and "https://[" not in detail.text


def test_invalid_postcode_has_linked_and_inline_recovery(directory):
    client, _db = directory
    page = client.get("/leg-verzeichnis?q=Fictional&plz=54")
    assert page.status_code == 400
    assert 'href="#plz"' in page.text and 'id="plz-error"' in page.text
    assert re.search(r'<input[^>]*id="plz"[^>]*aria-invalid="true"', page.text)
    assert 'value="Fictional"' in page.text and 'value="54"' in page.text


def test_directory_form_keeps_original_municipality_context_after_changing_filters(
    directory,
):
    client, _db = directory
    page = client.get("/leg-verzeichnis?bfs=4021&check_q=Baden&lang=en")
    hidden = dict(
        re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', page.text)
    )
    assert hidden["check_q"] == "Baden" and hidden["check_bfs"] == "4021"
    filtered = client.get(
        "/leg-verzeichnis", query_string={**hidden, "q": "Solar", "bfs": "9999"}
    )
    return_url = _href(filtered, "data-directory-check")
    assert parse_qs(urlsplit(return_url).query)["q"] == ["Baden"]
    assert parse_qs(urlsplit(return_url).query)["bfs"] == ["4021"]


def test_leg_check_links_published_entries_and_reports_a_failed_read(
    directory, monkeypatch
):
    client, db = directory
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE leg_registry SET ort='Fictional district'")
    monkeypatch.setattr(
        db,
        "search_municipality_profiles",
        lambda query: [
            {
                "name": "Fictional Baden",
                "bfs_number": 4021,
                "kanton": "AG",
                "pv_score_pct": None,
            }
        ],
    )
    monkeypatch.setattr(db, "get_elcom_tariffs", lambda bfs: [])
    page = client.get("/leg-check?q=Fictional+Baden&bfs=4021&lang=en")
    assert page.status_code == 200
    link = html.unescape(
        re.search(r'href="(/leg-verzeichnis/fictional[^\"]+)"', page.text)[1]
    )
    assert parse_qs(urlsplit(link).query)["bfs"] == ["4021"]
    detail = client.get(link)
    assert detail.status_code == 200
    back = client.get(_href(detail, "data-directory-back"))
    assert back.text.count("data-directory-entry=") == 25
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("DROP TABLE leg_registry")
    failed = client.get("/leg-check?q=Fictional+Baden&bfs=4021")
    assert failed.status_code == 503
    assert "data-registry-unavailable" in failed.text
