# SPDX-License-Identifier: AGPL-3.0-or-later
"""Search content: page text extraction and refresh of database-backed parts."""

import pytest

import site_search_index
from site_search import Document
from site_search_index import SiteSearch, extract_page

PAGE = """<!DOCTYPE html><html><head><title>Kosten | OpenLEG</title>
<meta name="description" content="Was  OpenLEG kostet."></head><body>
<nav><a href="/pricing">Kosten und Betrieb</a></nav>
<main id="main-content"><h1>Was OpenLEG kostet.</h1><p>Die Software ist <strong>frei</strong>.</p>
<section id="betrieb"><h2>Betrieb</h2><p>Server kosten Geld.</p>
<form><label>E-Mail</label><input name="email"></form>
<table><tr><td>Zeile 1</td></tr></table>
<p hidden>Versteckt</p><span aria-hidden="true">Deko</span><script>var x = "Skript";</script>
<details><summary>Was kostet der Betrieb?</summary><p>Das hängt vom Server ab.</p></details>
<details><summary>Aufgaben im Detail</summary><p>Rechnungen prüfen.</p></details>
<dl><div><dt>VNB</dt><dd>Verteilnetzbetreiber.</dd></div></dl></section>
<h2 id="mehr">Mehr</h2><p>Zum <a href="/self-host">Selbstbetrieb</a>.</p>
</main><footer><p>Fusszeile</p></footer></body></html>"""


def test_extraction_reads_the_main_text_split_at_headings():
    content = extract_page(PAGE)
    assert content.title == "Was OpenLEG kostet."
    assert content.head_title == "Kosten | OpenLEG"
    assert content.description == "Was OpenLEG kostet."
    assert [(block.heading, block.anchor, block.text) for block in content.blocks] == [
        ("", "", "Die Software ist frei."),
        (
            "Betrieb",
            "betrieb",
            "Server kosten Geld. Aufgaben im Detail Rechnungen prüfen.",
        ),
        ("Mehr", "mehr", "Zum Selbstbetrieb."),
    ]


def test_extraction_leaves_out_navigation_forms_tables_and_hidden_text():
    text = " ".join(block.text for block in extract_page(PAGE).blocks)
    for hidden in (
        "Kosten und Betrieb",
        "E-Mail",
        "Zeile",
        "Versteckt",
        "Deko",
        "Skript",
        "Fusszeile",
    ):
        assert hidden not in text


def test_questions_and_terms_become_their_own_entries():
    content = extract_page(PAGE)
    assert content.questions == [
        ("Was kostet der Betrieb?", "Das hängt vom Server ab.", "betrieb")
    ]
    assert content.definitions == [("VNB", "Verteilnetzbetreiber.", "betrieb")]
    assert ("/pricing", "Kosten und Betrieb") in content.links


def test_a_listing_page_keeps_only_its_title_and_headings():
    html = PAGE.replace('<h2 id="mehr">', '<h2 id="mehr">' + "x" * 60_000)
    content = extract_page(html, listing=True)
    assert content.title == "Was OpenLEG kostet."
    assert [block.heading for block in content.blocks] == ["Betrieb"]
    assert all(block.text == "" for block in content.blocks)
    assert content.questions == content.definitions == []


def test_page_documents_carry_link_labels_and_path_words():
    documents = site_search_index.documents_from_page(
        "/pricing", "page", "de_CH", extract_page(PAGE), labels={"Kosten und Betrieb"}
    )
    page, question, term = documents
    assert page.title == "Was OpenLEG kostet"
    assert page.keywords == ("Kosten", "pricing", "Kosten und Betrieb")
    assert (question.kind, question.title, question.context, question.anchor) == (
        "knowledge",
        "Was kostet der Betrieb?",
        "Was OpenLEG kostet",
        "betrieb",
    )
    assert (term.title, term.summary) == ("VNB", "Verteilnetzbetreiber.")
    assert question.group_key != term.group_key


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class _FakeApp:
    def __init__(self, *, testing=True):
        self.testing = testing

    def app_context(self):
        import contextlib

        return contextlib.nullcontext()


def _document(title, kind="municipality"):
    return Document(kind=kind, url="/" + title, title=title)


@pytest.fixture
def sources(monkeypatch):
    state = {
        "pages": ([_document("Startseite", "page")], True),
        "municipalities": [_document("Baden")],
        "legs": [],
        "calls": [],
    }

    def pages(_app, previous=()):
        state["calls"].append("pages")
        state["previous_pages"] = tuple(previous)
        return state["pages"]

    def municipalities():
        state["calls"].append("municipalities")
        value = state["municipalities"]
        if isinstance(value, Exception):
            raise value
        return value

    def legs():
        state["calls"].append("legs")
        return state["legs"]

    monkeypatch.setattr(site_search_index, "page_documents", pages)
    monkeypatch.setattr(site_search_index, "municipality_documents", municipalities)
    monkeypatch.setattr(site_search_index, "leg_documents", legs)
    return state


def _titles(search):
    return sorted(document.title for document in search.index().documents)


def test_database_parts_refresh_after_their_time_to_live(sources):
    clock = _Clock()
    search = SiteSearch(_FakeApp(), clock=clock)
    assert _titles(search) == ["Baden", "Startseite"]
    sources["municipalities"] = [_document("Bern")]
    clock.now += site_search_index.DATA_TTL_SECONDS - 1
    assert _titles(search) == ["Baden", "Startseite"]
    clock.now += 1
    assert _titles(search) == ["Bern", "Startseite"]
    assert sources["calls"].count("pages") == 2


def test_an_unavailable_source_keeps_the_previous_content_and_retries_soon(sources):
    clock = _Clock()
    search = SiteSearch(_FakeApp(), clock=clock)
    search.index()
    clock.now += site_search_index.DATA_TTL_SECONDS
    sources["municipalities"] = None
    assert _titles(search) == ["Baden", "Startseite"]
    sources["municipalities"] = RuntimeError("database down")
    clock.now += site_search_index.RETRY_SECONDS
    assert _titles(search) == ["Baden", "Startseite"]
    sources["municipalities"] = [_document("Chur")]
    clock.now += site_search_index.RETRY_SECONDS
    assert _titles(search) == ["Chur", "Startseite"]


def test_incomplete_pages_are_rendered_again_soon(sources):
    clock = _Clock()
    sources["pages"] = ([_document("Startseite", "page")], False)
    search = SiteSearch(_FakeApp(), clock=clock)
    search.index()
    sources["pages"] = (
        [_document("Startseite", "page"), _document("Verein", "page")],
        True,
    )
    clock.now += site_search_index.RETRY_SECONDS
    assert "Verein" in _titles(search)


def test_warming_builds_the_index_in_the_background(sources):
    search = SiteSearch(_FakeApp(testing=False))
    search.warm()
    # index() waits for the running build instead of starting another.
    assert _titles(search) == ["Baden", "Startseite"]
    assert sources["calls"].count("pages") == 1
    search.warm()
    assert sources["calls"].count("pages") == 1


def test_municipality_postcodes_are_grouped_by_locality(monkeypatch):
    monkeypatch.setattr(
        site_search_index.db,
        "get_all_municipality_profiles",
        lambda **_kwargs: [{"bfs_number": 4021, "name": "Baden", "kanton": "AG"}],
    )
    monkeypatch.setattr(
        site_search_index,
        "postcodes_by_municipality",
        lambda: {
            4021: (
                ("5400", 100, ("Baden",)),
                ("5404", 100, ("Baden",)),
                ("5405", 100, ("Dättwil AG",)),
                ("5406", 92, ()),
            )
        },
    )
    (baden,) = site_search_index.municipality_documents()
    assert baden.summary == "5400, 5404, 5406 Baden · 5405 Dättwil AG"
    assert baden.boosts == {"5400": 2.0, "5404": 2.0, "5405": 2.0, "5406": 1.84}
    assert "Dättwil AG" in baden.keywords and "5406" in baden.keywords


def test_listing_pages_without_text_are_not_merged():
    content = extract_page(PAGE, listing=True)
    first = site_search_index.documents_from_page(
        "/rangliste", "page", "de_CH", content
    )
    second = site_search_index.documents_from_page(
        "/rangliste/vergleich", "page", "de_CH", content
    )
    assert first[0].duplicate_key == second[0].duplicate_key == ""


def test_withdrawn_legs_disappear_and_an_unavailable_directory_hides_them(sources):
    clock = _Clock()
    sources["legs"] = [_document("LEG Sonnenhalde", "leg")]
    search = SiteSearch(_FakeApp(), clock=clock)
    assert "LEG Sonnenhalde" in _titles(search)
    sources["legs"] = []
    clock.now += site_search_index.LEG_TTL_SECONDS
    assert "LEG Sonnenhalde" not in _titles(search)
    sources["legs"] = [_document("LEG Sonnenhalde", "leg")]
    clock.now += site_search_index.LEG_TTL_SECONDS
    assert "LEG Sonnenhalde" in _titles(search)
    # When the directory cannot confirm publication, its entries are hidden.
    sources["legs"] = None
    clock.now += site_search_index.LEG_TTL_SECONDS
    assert "LEG Sonnenhalde" not in _titles(search)


def test_expired_legs_are_hidden_while_their_refresh_is_pending(sources):
    import threading

    clock = _Clock()
    release = threading.Event()
    sources["legs"] = [_document("LEG Sonnenhalde", "leg")]
    search = SiteSearch(_FakeApp(testing=False), clock=clock)
    assert search.search("Sonnenhalde", locale="de_CH").total == 1
    original_legs = site_search_index.leg_documents

    def slow_legs():
        release.wait(5)
        return original_legs()

    site_search_index.leg_documents = slow_legs
    try:
        # The refresh is due, but within the grace period the entry still shows.
        clock.now += site_search_index.LEG_TTL_SECONDS
        assert search.search("Sonnenhalde", locale="de_CH").total == 1
        # Past it, the pending refresh no longer vouches for the entry.
        clock.now += site_search_index.LEG_GRACE_SECONDS
        assert search.search("Sonnenhalde", locale="de_CH").total == 0
        assert search.search("Sonnenhalde", locale="de_CH", kinds={"leg"}).total == 0
    finally:
        site_search_index.leg_documents = original_legs
        release.set()
    for _ in range(100):
        if not search._refreshing:
            break
        threading.Event().wait(0.05)
    assert search.search("Sonnenhalde", locale="de_CH").total == 1


def test_an_unchanged_refresh_keeps_the_index(sources):
    clock = _Clock()
    search = SiteSearch(_FakeApp(), clock=clock)
    first = search.index()
    clock.now += site_search_index.LEG_TTL_SECONDS
    assert search.index() is first
    sources["legs"] = [_document("LEG Neu", "leg")]
    clock.now += site_search_index.LEG_TTL_SECONDS
    assert search.index() is not first


def test_a_slow_rebuild_cannot_extend_an_overdue_leg(sources, monkeypatch):
    import threading

    clock = _Clock()
    sources["legs"] = [_document("LEG Sonnenhalde", "leg")]
    search = SiteSearch(_FakeApp(testing=False), clock=clock)
    assert search.search("Sonnenhalde", locale="de_CH").total == 1
    release = threading.Event()
    building = threading.Event()
    original = site_search_index.SearchIndex

    def slow_index(documents):
        building.set()
        release.wait(5)
        return original(documents)

    monkeypatch.setattr(site_search_index, "SearchIndex", slow_index)
    sources["legs"] = []
    clock.now += site_search_index.LEG_TTL_SECONDS
    search.search("Sonnenhalde", locale="de_CH")
    assert building.wait(5)
    # The new expiry belongs to the index still being built, not to the one
    # that answers now.
    clock.now += site_search_index.LEG_GRACE_SECONDS
    try:
        assert search.search("Sonnenhalde", locale="de_CH").total == 0
    finally:
        release.set()


def test_a_failed_render_keeps_the_page_from_the_last_refresh(monkeypatch):
    """One page that fails to render keeps its documents from the last refresh."""
    monkeypatch.setattr(
        site_search_index, "PAGES", (("/pricing", "page"), ("/verein", "page"))
    )
    monkeypatch.setattr(site_search_index, "LISTING_PAGES", ())
    monkeypatch.setattr(site_search_index.i18n, "supported_locales", lambda: ("de_CH",))
    monkeypatch.setattr(
        site_search_index.tenant_module, "get_tenant_config", lambda *_a, **_k: {}
    )
    monkeypatch.setattr(
        site_search_index,
        "render_public_page",
        lambda _app, path, _locale, _tenant: PAGE if path == "/pricing" else None,
    )
    previous = (
        Document(kind="page", url="/verein", title="Verein", locale="de_CH"),
        Document(kind="page", url="/verein", title="Association", locale="fr_CH"),
        Document(kind="page", url="/pricing", title="Alte Kosten", locale="de_CH"),
    )
    documents, complete = site_search_index.page_documents(_FakeApp(), previous)
    titles = {(document.url, document.title) for document in documents}
    assert ("/verein", "Verein") in titles, "the failed page keeps its last text"
    assert ("/verein", "Association") not in titles, "only the same language"
    assert ("/pricing", "Was OpenLEG kostet") in titles
    assert ("/pricing", "Alte Kosten") not in titles, "a rendered page is fresh"
    assert complete is False


def test_the_page_refresh_receives_the_last_good_documents(sources):
    clock = _Clock()
    search = SiteSearch(_FakeApp(), clock=clock)
    search.index()
    assert sources["previous_pages"] == ()
    clock.now += site_search_index.PAGE_TTL_SECONDS
    search.index()
    assert [d.title for d in sources["previous_pages"]] == ["Startseite"]


@pytest.mark.parametrize("pages_available, warned", [(1, False), (5, True)])
def test_a_directory_longer_than_the_page_limit_is_reported(
    monkeypatch, caplog, pages_available, warned
):
    monkeypatch.setattr(site_search_index, "MAX_DIRECTORY_PAGES", 2)

    def read_public_registry(*, page=1, **_kwargs):
        return {
            "entries": [{"slug": f"leg-{page}", "name": f"LEG {page}"}],
            "has_next": page < pages_available,
        }

    monkeypatch.setattr(
        site_search_index.db, "read_public_registry", read_public_registry
    )
    with caplog.at_level("WARNING", logger=site_search_index.logger.name):
        documents = site_search_index.leg_documents()
    assert len(documents) == min(pages_available, 2)
    assert ("more than 2 pages" in caplog.text) is warned
