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


# Current catalogue wording for each directory state, per display language.
STATE_TEXT = {
    "de-CH": {
        "not_found": "Seite nicht gefunden",
        "empty": "Keine LEGs gefunden",
        "undated": "nicht angegeben",
        "self_report": "Moderierte Selbstauskunft",
        "unavailable": "Dienst vorübergehend nicht verfügbar",
        "claimed": "Vom Betreiber bestätigt",
        "claimed_on": "Vom Betreiber beansprucht am",
        "last_confirmed": "Zuletzt bestätigt am",
        "never_reconfirmed": "Noch nie erneut bestätigt.",
        "no_eligibility": "OpenLEG prüft keine Netz-Topologie und bestätigt nicht",
    },
    "en": {
        "not_found": "Page not found",
        "empty": "No LEGs found",
        "undated": "Not specified",
        "self_report": "Moderated self-information",
        "unavailable": "Service temporarily unavailable",
        "claimed": "Confirmed by the operator",
        "claimed_on": "Claimed by the operator on",
        "last_confirmed": "Last confirmed on",
        "never_reconfirmed": "Never reconfirmed.",
        "no_eligibility": "OpenLEG does not check a network topology",
    },
    "fr-CH": {
        "not_found": "Page introuvable",
        "empty": "Aucune CEL trouvée",
        "undated": "non renseigné",
        "self_report": "Déclaration modérée",
        "unavailable": "Service temporairement indisponible",
        "claimed": "Confirmé par l’exploitant",
        "claimed_on": "Revendiqué par l’exploitant le",
        "last_confirmed": "Dernière confirmation le",
        "never_reconfirmed": "Jamais reconfirmé.",
        "no_eligibility": "OpenLEG ne vérifie pas la topologie du réseau",
    },
    "it-CH": {
        "not_found": "Pagina non trovata",
        "empty": "Nessun LEGs trovato",
        "undated": "non specificato",
        "self_report": "L'autoinformazione moderato",
        "unavailable": "Servizio temporaneamente non disponibile",
        "claimed": "Confermata dall'operatore",
        "claimed_on": "Rivendicata dall'operatore il",
        "last_confirmed": "Ultimo confermato il",
        "never_reconfirmed": "Mai riconfermata.",
        "no_eligibility": "OpenLEG non verifica la topologia della rete",
    },
}


def _localized(response, language):
    assert response.headers["Content-Language"] == language
    return html.unescape(response.text)


@pytest.mark.parametrize("language", sorted(STATE_TEXT))
def test_language_change_keeps_search_and_bfs_on_results_and_details(
    directory, language
):
    client, _db = directory
    query = {"q": "Fictional", "bfs": "4021", "check_q": "Baden", "page": "2"}
    origin = "fr-CH" if language == "en" else "en"
    result = client.get("/leg-verzeichnis", query_string={**query, "lang": origin})
    detail = client.get(_href(result, "data-directory-entry"))
    for page, attribute in [
        (result, "data-directory-entry"),
        (detail, "data-directory-back"),
    ]:
        switch = html.unescape(
            re.search(
                r'href="([^"]+)" data-language-choice lang="' + language + '"',
                page.text,
            )[1]
        )
        switched = client.get(switch)
        assert switched.status_code == 200
        _localized(switched, language)
        assert urlsplit(switch).path == urlsplit(page.request.url).path
        link = parse_qs(urlsplit(_href(switched, attribute)).query)
        # German-default installations keep historical links without lang.
        assert link.get("lang", ["de-CH"]) == [language] and link["page"] == ["2"]
        assert link["q"] == ["Fictional"] and link["bfs"] == ["4021"]
        assert link["check_q"] == ["Baden"] and link["check_bfs"] == ["4021"]


@pytest.mark.parametrize("language", sorted(STATE_TEXT))
def test_pending_rejected_and_missing_entries_stay_hidden_in_every_language(
    directory, language
):
    client, _db = directory
    text = STATE_TEXT[language]
    missing = client.get("/leg-verzeichnis/missing", query_string={"lang": language})
    assert missing.status_code == 404
    assert text["not_found"] in _localized(missing, language)
    # fictional-26 is pending and fictional-27 rejected in the fixture.
    for slug, name in [
        ("fictional-26", "Fictional LEG 26"),
        ("fictional-27", "Fictional LEG 27"),
    ]:
        detail = client.get("/leg-verzeichnis/" + slug, query_string={"lang": language})
        assert detail.status_code == 404
        page = _localized(detail, language)
        assert text["not_found"] in page and name not in page
        search = client.get(
            "/leg-verzeichnis", query_string={"q": name, "lang": language}
        )
        assert search.status_code == 200
        page = _localized(search, language)
        assert text["empty"] in page and slug not in page
        assert "data-directory-entry=" not in page


@pytest.mark.parametrize("language", sorted(STATE_TEXT))
def test_sparse_entry_lists_and_details_without_missing_values_in_every_language(
    directory, language
):
    client, db = directory
    text = STATE_TEXT[language]
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO leg_registry(slug,name,contact_email,
            moderation_status,leg_status,created_at,updated_at)
            VALUES ('sparse','Fictional sparse LEG','private@example.invalid',
            'published',NULL,NULL,NULL)""")
    result = client.get(
        "/leg-verzeichnis", query_string={"q": "sparse", "lang": language}
    )
    assert result.status_code == 200
    page = _localized(result, language)
    assert page.count("data-directory-entry=") == 1
    assert text["self_report"] in page and ">None<" not in page
    detail = client.get(_href(result, "data-directory-entry"))
    assert detail.status_code == 200
    page = _localized(detail, language)
    assert "Fictional sparse LEG" in page and text["undated"] in page
    assert text["self_report"] in page and ">None<" not in page
    assert text["never_reconfirmed"] in page and text["no_eligibility"] in page
    assert "private@example.invalid" not in page
    back = _href(detail, "data-directory-back")
    assert parse_qs(urlsplit(back).query)["q"] == ["sparse"]


@pytest.mark.parametrize("language", sorted(STATE_TEXT))
def test_claimed_entry_explains_ownership_freshness_and_no_eligibility(
    directory, language
):
    client, _db = directory
    text = STATE_TEXT[language]
    detail = client.get(
        "/leg-verzeichnis/fictional-00", query_string={"lang": language}
    )
    assert detail.status_code == 200
    page = _localized(detail, language)
    trust = re.search(r'data-testid="registry-trust"(.*?)</div>', page, re.DOTALL)[1]
    for key in ["claimed", "claimed_on", "last_confirmed"]:
        assert text[key] in trust, key
    assert text["self_report"] not in trust and text["never_reconfirmed"] not in trust
    assert text["no_eligibility"] in page


@pytest.mark.parametrize("language", sorted(STATE_TEXT))
def test_unavailable_storage_is_not_reported_as_empty_in_every_language(
    directory, language, monkeypatch
):
    client, db = directory
    text = STATE_TEXT[language]
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
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("DROP TABLE leg_registry")
    for path, query in [
        ("/leg-verzeichnis", {"q": "Fictional"}),
        ("/leg-verzeichnis/fictional-00", {}),
        ("/leg-check", {"q": "Fictional Baden", "bfs": "4021"}),
    ]:
        response = client.get(path, query_string={**query, "lang": language})
        assert response.status_code == 503
        page = _localized(response, language)
        assert text["unavailable"] in page and text["empty"] not in page
        assert "data-directory-entry=" not in page


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


def test_municipality_profile_links_published_details_without_submission(
    directory, monkeypatch
):
    client, db = directory
    profile = {"bfs_number": 4021, "name": "Fictional Baden", "kanton": "AG"}
    monkeypatch.setattr(db, "get_municipality_profile", lambda bfs: profile)
    monkeypatch.setattr(db, "get_elcom_tariffs", lambda *args, **kwargs: [])
    monkeypatch.setattr(db, "get_sonnendach_municipal", lambda bfs: None)
    monkeypatch.setattr(db, "get_interest_count", lambda bfs, **kwargs: 0)
    page = client.get("/gemeinde/profil/4021")
    assert page.status_code == 200
    links = re.findall(r'href="(/leg-verzeichnis[^"]*)"', page.text)
    assert "/leg-verzeichnis/fictional-00" in links
    assert "fictional-26" not in page.text and "fictional-27" not in page.text
    assert "/leg-verzeichnis/eintragen" not in page.text
    assert "/beanspruchen" not in page.text
    assert client.get("/leg-verzeichnis/fictional-00").status_code == 200
