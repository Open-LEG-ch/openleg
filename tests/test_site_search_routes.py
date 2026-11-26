# SPDX-License-Identifier: AGPL-3.0-or-later
"""/suche: public results in every language, escaped, without private data."""

import re
from html import unescape
from urllib.parse import urlsplit

import pytest

from tests import test_i18n

app_module = test_i18n.app_module
isolated_environment = test_i18n.isolated_environment
web = test_i18n.web

PROFILES = [
    {"bfs_number": 261, "name": "Zürich", "kanton": "ZH"},
    {"bfs_number": 4021, "name": "Baden", "kanton": "AG"},
    {"bfs_number": 351, "name": "Bern", "kanton": "BE"},
    {"bfs_number": 6621, "name": "Genève", "kanton": "GE"},
    *(
        {"bfs_number": 9000 + number, "name": f"Testdorf {number}", "kanton": "ZH"}
        for number in range(30)
    ),
]
PUBLIC_COLUMNS = {
    "bfs_number": None,
    "member_count_estimate": None,
    "website_url": "",
    "source": "self_submitted",
    "claimed_at": None,
    "last_verified_at": None,
    "created_at": None,
    "updated_at": None,
}
DIRECTORY = [
    {
        **PUBLIC_COLUMNS,
        "slug": "sonnenhalde",
        "name": "LEG Sonnenhalde",
        "kanton": "AG",
        "plz": "5400",
        "ort": "Baden",
        "vnb_name": "Regionalwerke AG Baden",
        "leg_status": "aktiv",
        "description": "Nachbarn teilen Solarstrom am Hang.",
        # Private registry columns must never reach the index, even if a
        # reader returned them by mistake.
        "contact_email": "geheim@example.org",
        "claim_token": "token-geheim-123",
    },
    {
        **PUBLIC_COLUMNS,
        "slug": "boese",
        "name": "<script>alert('leg')</script>Quartier",
        "kanton": "ZH",
        "plz": "8001",
        "ort": "Zürich",
        "vnb_name": "",
        "leg_status": "planung",
        "description": "<img src=x onerror=alert(1)> Quartierstrom",
    },
]


@pytest.fixture
def search_web(web, monkeypatch):
    import database
    import homepage_view_model

    web.config["SITE_RELEASE_ENABLED"] = True
    monkeypatch.setattr(
        database, "get_stats", lambda city_id=None: {"total_buildings": 0}
    )
    monkeypatch.setattr(homepage_view_model, "ranking_extremes", lambda: ([], [], 0))
    monkeypatch.setattr(
        database,
        "get_all_municipality_profiles",
        lambda **_kwargs: [dict(p) for p in PROFILES],
    )

    def read_public_registry(*, filters=None, slug=None, page=1):
        assert filters in (None, {}) and slug is None
        return {
            "entries": [dict(entry) for entry in DIRECTORY] if page == 1 else [],
            "has_next": False,
        }

    monkeypatch.setattr(database, "read_public_registry", read_public_registry)
    return web


def _results(html):
    """Result titles and their links, in page order."""
    return [
        (unescape(re.sub(r"<[^>]+>", "", title)), unescape(href))
        for href, title in re.findall(
            r'<a class="site-search-title" href="([^"]+)">(.*?)</a>', html, re.DOTALL
        )
    ]


def test_search_belongs_to_the_complete_site(web):
    assert web.test_client().get("/suche?q=LEG").status_code == 404


def test_empty_search_page_is_labelled_and_not_indexed(search_web):
    response = search_web.test_client().get("/suche")
    assert response.status_code == 200
    assert response.headers["X-Robots-Tag"] == "noindex"
    html = response.text
    assert '<meta name="robots" content="noindex, follow">' in html
    assert '<label for="site-search-page-q">Suchbegriff</label>' in html
    assert 'role="search"' in html and 'action="/suche"' in html
    assert 'name="q"' in html and 'maxlength="100"' in html
    assert 'href="/suche?q=Smart+Meter"' in html


def test_a_municipality_is_found_by_name_and_postcode(search_web):
    client = search_web.test_client()
    html = client.get("/suche?q=Zuerich").text
    assert ("Zürich", "/gemeinde/profil/261") in _results(html)
    assert "<mark>Zürich</mark>" in html
    assert "Gemeinde im Kanton ZH" in html
    html = client.get("/suche?q=8001").text
    assert _results(html)[0] == ("Zürich", "/gemeinde/profil/261")
    assert "<mark>8001</mark>" in html
    assert "amtliches Gebäudeadressverzeichnis von swisstopo" in html


def test_pages_questions_and_terms_are_found(search_web):
    client = search_web.test_client()
    assert ("Begriffe rund um Ihre LEG", "/wissen/glossar") in _results(
        client.get("/suche?q=Glossar").text
    )
    html = client.get("/suche?q=Messpunkt").text
    assert ("Messpunkt", "/wissen/glossar") in _results(html)
    html = client.get("/suche?q=Was+kostet+OpenLEG").text
    question = [title for title, _href in _results(html) if title.endswith("?")]
    assert "Was kostet OpenLEG?" in question
    # A section hit links to its heading.
    html = client.get("/suche?q=Häufige+Fragen").text
    assert any(href.startswith("/how-it-works#") for _title, href in _results(html))


def test_a_published_leg_is_found(search_web):
    html = search_web.test_client().get("/suche?q=Sonnenhalde").text
    assert ("LEG Sonnenhalde", "/leg-verzeichnis/sonnenhalde") in _results(html)
    assert "5400 Baden · AG · aktiv" in html


def test_results_and_queries_are_escaped(search_web):
    client = search_web.test_client()
    html = client.get("/suche?q=Quartier").text
    assert "<script>alert" not in html and "<img src=x" not in html
    assert (
        "&lt;script&gt;alert(&#39;leg&#39;)&lt;/script&gt;<mark>Quartier</mark>" in html
    )
    attack = '"><svg onload=alert(1)>'
    html = client.get("/suche", query_string={"q": attack}).text
    assert "<svg onload" not in html
    assert 'value="&#34;&gt;&lt;svg onload=alert(1)&gt;"' in html


def test_private_registry_fields_are_not_searchable(search_web):
    import site_search_routes

    client = search_web.test_client()
    html = client.get("/suche", query_string={"q": "geheim"}).text
    assert _results(html) == []
    with search_web.app_context():
        index = site_search_routes.site_search().index()
    for document in index.documents:
        text = repr(document)
        assert "geheim" not in text and "claim_token" not in text


def test_pages_are_indexed_as_an_anonymous_visitor(search_web):
    client = search_web.test_client()
    with client.session_transaction() as session:
        session["dashboard_building_id"] = "building-1"
    # Signed in, "/" redirects to the dashboard. The index still has the
    # public homepage, because it renders pages without the visitor's session.
    assert client.get("/").status_code == 302
    titles = _results(client.get("/suche?q=Stromgemeinschaft").text)
    assert any(urlsplit(href).path == "/" for _title, href in titles)


def test_each_listing_page_is_found_in_the_current_language(search_web):
    html = search_web.test_client().get("/suche?q=Rangliste").text
    urls = {urlsplit(href).path for _title, href in _results(html)}
    assert {"/rangliste", "/rangliste/fortschritte", "/rangliste/vergleich"} <= urls
    assert 'class="site-search-language"' not in html


def test_only_public_routes_are_indexed(search_web):
    import site_search_routes

    with search_web.app_context():
        index = site_search_routes.site_search().index()
    urls = {document.url for document in index.documents}
    private = (
        "/dashboard",
        "/admin",
        "/api/admin",
        "/verein/mitglied",
        "/verein/vorstand",
        "/sandbox",
        "/leg/",
        "/utility",
        "/gemeinde/dashboard",
        "/unsubscribe",
        "/confirm",
        "/meter-upload",
    )
    assert not [url for url in urls if url.startswith(private)]
    assert {
        "/",
        "/wissen/glossar",
        "/gemeinde/profil/261",
        "/leg-verzeichnis/sonnenhalde",
    } <= urls


@pytest.mark.parametrize(
    ("locale", "label", "query", "title", "href"),
    [
        ("de-CH", "Suchbegriff", "Messpunkt", "Messpunkt", "/wissen/glossar"),
        (
            "fr-CH",
            "Terme de recherche",
            "Zurich",
            "Zürich",
            "/gemeinde/profil/261?lang=fr-CH",
        ),
        ("it-CH", "Termine di ricerca", "Zurigo", None, None),
        ("en", "Search term", "Zurich", "Zürich", "/gemeinde/profil/261?lang=en"),
    ],
)
def test_every_language_has_its_own_labels_and_links(
    search_web, locale, label, query, title, href
):
    response = search_web.test_client().get(
        "/suche", query_string={"q": query, "lang": locale}
    )
    assert response.status_code == 200
    html = response.text
    assert f'<html lang="{locale}"' in html
    assert f'<label for="site-search-page-q">{label}</label>' in html
    assert f'<input type="hidden" name="lang" value="{locale}">' in html
    if title:
        assert (title, href) in _results(html)


def test_current_language_results_come_first(search_web):
    client = search_web.test_client()
    html = client.get("/suche", query_string={"q": "Glossar", "lang": "fr-CH"}).text
    results = _results(html)
    assert results and results[0][1].startswith("/wissen/glossar?lang=fr-CH")
    assert (
        'lang="de-CH"' not in html.split("site-search-results", 1)[1].split("</ol>")[0]
    )


def test_a_match_only_in_another_language_is_marked(search_web):
    html = search_web.test_client().get("/suche?q=metering").text
    item = html.split('<li class="site-search-result" lang="en">', 1)[1].split(
        "</li>", 1
    )[0]
    assert '<span class="site-search-language">English</span>' in item
    assert "?lang=en" in item


def test_near_misses_offer_a_correction(search_web):
    html = search_web.test_client().get("/suche?q=Glosar").text
    assert "Meinten Sie «glossar»?" in unescape(html).replace("Glossar", "glossar")
    assert re.search(r'href="/suche\?q=[Gg]lossar"', html)


def test_unknown_words_are_reported_and_left_out(search_web):
    html = unescape(search_web.test_client().get("/suche?q=Messpunkt+qqqqq").text)
    assert "«qqqqq» kommt auf der Website nicht vor." in html
    assert ("Messpunkt", "/wissen/glossar") in _results(html)


def test_nothing_found_offers_examples_and_shortcuts(search_web):
    html = unescape(search_web.test_client().get("/suche?q=qqqqq").text)
    assert "Keine Treffer für «qqqqq»" in html
    assert 'href="/suche?q=Netzbetreiber"' in html
    assert 'href="/leg-gruenden"' in html


def test_a_group_can_be_paged(search_web):
    client = search_web.test_client()
    html = client.get("/suche?q=Testdorf").text
    assert len(re.findall(r'href="/gemeinde/profil/9\d{3}"', html)) == 8
    assert 'href="/suche?q=Testdorf&amp;typ=gemeinden"' in html
    html = client.get("/suche?q=Testdorf&typ=gemeinden").text
    assert len(re.findall(r'href="/gemeinde/profil/9\d{3}"', html)) == 20
    assert 'href="/suche?q=Testdorf&amp;typ=gemeinden&amp;seite=2"' in html
    html = client.get("/suche?q=Testdorf&typ=gemeinden&seite=2").text
    assert len(re.findall(r'href="/gemeinde/profil/9\d{3}"', html)) == 10
    assert "seite=3" not in html
    for page in ("0", "x", "99999"):
        assert (
            client.get(f"/suche?q=Testdorf&typ=gemeinden&seite={page}").status_code
            == 200
        )


def test_query_length_is_capped(search_web):
    html = search_web.test_client().get("/suche", query_string={"q": "a" * 500}).text
    assert 'value="' + "a" * 100 + '"' in html
    assert "«" + "a" * 100 + "»" in unescape(html)


def test_search_is_rate_limited(search_web):
    client = search_web.test_client()
    statuses = [client.get("/suche").status_code for _ in range(241)]
    assert statuses[:240] == [200] * 240
    assert statuses[240] == 429


def test_navigation_field_is_a_labelled_get_form(search_web):
    from flask import render_template

    with search_web.test_request_context("/?lang=fr-CH"):
        search_web.preprocess_request()
        html = render_template("partials/site_search.html")
    assert (
        '<form class="site-search" role="search" method="get" action="/suche"' in html
    )
    assert '<label class="site-search-label" for="site-search-q">' in html
    assert 'id="site-search-q"' in html and 'name="q"' in html
    assert '<input type="hidden" name="lang" value="fr-CH">' in html
    assert '<link rel="stylesheet" href="/static/css/site_search.css">' in html
    search_web.config["SITE_RELEASE_ENABLED"] = False
    with search_web.test_request_context("/"):
        assert render_template("partials/site_search.html").strip() == ""


def test_rendering_pages_spends_no_rate_limit(search_web):
    import site_search_index
    import tenant
    from security_extensions import rate_limit

    @rate_limit("1 per hour")
    def limited_probe():
        return "<html><body><main><h1>Probe</h1></main></body></html>"

    search_web.add_url_rule("/probe-limited", "limited_probe", limited_probe)
    for _ in range(3):
        html = site_search_index.render_public_page(
            search_web, "/probe-limited", "de_CH", tenant.DEFAULT_TENANT
        )
        assert html and "Probe" in html
    client = search_web.test_client()
    assert client.get("/probe-limited").status_code == 200
    assert client.get("/probe-limited").status_code == 429


def test_a_group_with_no_hits_shows_the_empty_state(search_web):
    html = unescape(
        search_web.test_client().get("/suche?q=Sonnenhalde&typ=gemeinden").text
    )
    assert "Keine Treffer für «Sonnenhalde»" in html
    assert 'href="/suche?q=Netzbetreiber"' in html


@pytest.mark.parametrize("page", ["9" * 5000, "-1", "1e3", "٣"])
def test_odd_page_numbers_fall_back_safely(search_web, page):
    response = search_web.test_client().get(
        "/suche", query_string={"q": "Testdorf", "typ": "gemeinden", "seite": page}
    )
    assert response.status_code == 200


def test_the_last_page_offers_no_further_page(search_web, monkeypatch):
    import site_search_routes

    monkeypatch.setattr(site_search_routes, "MAX_GROUP_PAGES", 1)
    html = search_web.test_client().get("/suche?q=Testdorf&typ=gemeinden").text
    assert "seite=2" not in html


def test_highlighted_titles_keep_their_spaces(search_web):
    html = search_web.test_client().get("/suche?q=Rangliste").text
    titles = re.findall(
        r'<a class="site-search-title" href="[^"]+">(.*?)</a>', html, re.DOTALL
    )
    assert titles and all(
        title.startswith("<span>") and title.endswith("</span>") for title in titles
    )
