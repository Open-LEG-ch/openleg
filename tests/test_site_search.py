# SPDX-License-Identifier: AGPL-3.0-or-later
"""Site search engine: folding, matching, synonyms, ranking and snippets."""

import pytest

from site_search import Block, Document, SearchIndex, fold


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        ("Zürich", "ZUERICH"),
        ("zurich", "Zürich"),
        ("Zuerich", "Zürich"),
        ("Grösse", "Größe"),
        ("Genève", "geneve"),
        ("Neuchâtel", "NEUCHATEL"),
        ("électricité", "Electricite"),
        ("città", "Citta"),
        ("Oeffentlich", "öffentlich"),
        ("Cœur", "coeur"),
    ],
)
def test_folding_treats_case_accents_and_umlaut_spellings_alike(typed, stored):
    assert fold(typed) == fold(stored)


@pytest.mark.parametrize(("left", "right"), [("Bach", "Buch"), ("Rat", "Rot")])
def test_folding_keeps_different_vowels_apart(left, right):
    assert fold(left) != fold(right)


def _page(title, text="", *, url=None, locale="de_CH", kind="page", **extra):
    return Document(
        kind=kind,
        url=url or "/" + title.lower().replace(" ", "-"),
        title=title,
        locale=locale,
        blocks=(Block(text=text),) if text else (),
        **extra,
    )


def _titles(result, kind=None):
    groups = result.groups if kind is None else (result.group(kind),)
    return [hit.document.title for group in groups if group for hit in group.hits]


def _municipality(name, kanton, bfs, postcodes=(), share=None):
    share = share or {}
    return Document(
        kind="municipality",
        url=f"/gemeinde/profil/{bfs}",
        title=name,
        keywords=(kanton, *postcodes),
        summary=", ".join(postcodes),
        summary_fallback=False,
        boosts={code: share.get(code, 100) / 50 for code in postcodes},
    )


def test_prefix_completes_the_word_being_typed():
    index = SearchIndex([_page("Rangliste"), _page("Software")])
    assert _titles(index.search("rang")) == ["Rangliste"]
    assert _titles(index.search("Ra")) == ["Rangliste"]


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("Gemiende", "Gemeinde"),
        ("Glosar", "Glossar"),
        ("Verteilnezbetreiber", "Verteilnetzbetreiber"),
    ],
)
def test_longer_words_tolerate_typos(typed, expected):
    index = SearchIndex([_page(expected), _page("Software")])
    assert _titles(index.search(typed)) == [expected]


def test_typos_apply_only_when_the_typed_word_is_unknown():
    index = SearchIndex([_page("Bern"), _page("Kern"), _page("Bar")])
    assert _titles(index.search("Bern")) == ["Bern"]
    assert _titles(index.search("Ber")) == ["Bern"]
    assert sorted(_titles(index.search("Xern"))) == ["Bern", "Kern"]


def test_words_up_to_three_letters_need_the_exact_letters():
    index = SearchIndex([_page("Bern")])
    assert index.search("Bor").total == 0


def test_two_typos_are_tolerated_only_above_seven_letters():
    index = SearchIndex([_page("Abrechnung"), _page("Messdaten")])
    assert _titles(index.search("Abrxyznung")) == []
    assert _titles(index.search("Abrehcnugn")) == ["Abrechnung"]
    assert _titles(index.search("Abrchnung")) == ["Abrechnung"]
    assert _titles(index.search("Abrechnnug")) == ["Abrechnung"]
    assert _titles(index.search("Mesdatn")) == []


def test_every_word_must_match():
    index = SearchIndex(
        [
            _page("Kosten", "Software und Betrieb kosten Geld."),
            _page("Betrieb", "Ein Server braucht Pflege."),
        ]
    )
    assert _titles(index.search("Betrieb Kosten")) == ["Kosten"]
    assert _titles(index.search("Betrieb")) == ["Betrieb", "Kosten"]


def test_filler_words_do_not_block_a_question():
    index = SearchIndex([_page("Kosten", "Was OpenLEG kostet."), _page("Betrieb")])
    assert _titles(index.search("was kostet openleg")) == ["Kosten"]
    assert index.search("Was kostet der Betrieb").total == 0


@pytest.mark.parametrize(
    ("query", "text"),
    [
        ("LEG", "Eine lokale Elektrizitätsgemeinschaft teilt Strom."),
        ("lokale Elektrizitätsgemeinschaft", "Die LEG teilt Strom."),
        ("CEL", "Une communauté électrique locale partage le courant."),
        ("local electricity community", "Die LEG teilt Strom."),
        ("VNB", "Fragen Sie den Netzbetreiber."),
        ("DSO", "Der Verteilnetzbetreiber misst."),
        ("gestionnaire du réseau", "Der VNB misst."),
        ("Netzbetreiber", "Le gestionnaire de réseau mesure."),
        ("Smart Meter", "Ein intelligentes Messsystem misst."),
        ("intelligentes Messsystem", "Der Smart-Meter misst."),
        ("ZEV", "Ein Zusammenschluss zum Eigenverbrauch."),
        ("vZEV", "Ein virtueller ZEV teilt Anschlussleitungen."),
    ],
)
def test_domain_synonyms_find_each_other(query, text):
    index = SearchIndex([_page("Ergebnis", text), _page("Anderes", "Nichts davon.")])
    assert _titles(index.search(query)) == ["Ergebnis"]


def test_a_synonym_hit_is_highlighted_in_the_snippet():
    index = SearchIndex([_page("Ergebnis", "Fragen Sie den Netzbetreiber zuerst.")])
    hit = index.search("VNB").groups[0].hits[0]
    assert [segment.text for segment in hit.snippet if segment.match] == [
        "Netzbetreiber"
    ]


def test_ranking_prefers_exact_title_then_title_prefix_heading_and_body():
    index = SearchIndex(
        [
            _page("Wissen", "Messdaten verstehen und nutzen."),
            Document(
                kind="page",
                url="/heading",
                title="Andere Seite",
                locale="de_CH",
                blocks=(Block(text="Erklärung.", heading="Messdaten"),),
            ),
            _page("Messdaten importieren"),
            _page("Messdaten"),
        ]
    )
    assert _titles(index.search("Messdaten")) == [
        "Messdaten",
        "Messdaten importieren",
        "Andere Seite",
        "Wissen",
    ]


def test_current_locale_comes_first_and_each_page_appears_once():
    def version(locale, title, text):
        return _page(title, text, url="/kontakt", locale=locale, group_key="/kontakt")

    index = SearchIndex(
        [
            version("de_CH", "Kontakt", "Schreiben Sie uns eine Nachricht."),
            version("fr_CH", "Contact", "Écrivez-nous un message."),
            _page("Message", "Ein message auf Deutsch.", url="/message"),
            _page("Mitteilung", "Un message.", url="/mitteilung", locale="fr_CH"),
        ]
    )
    hits = index.search("message", locale="de_CH").groups[0].hits
    assert [(hit.document.title, hit.secondary) for hit in hits] == [
        ("Message", False),
        ("Contact", True),
        ("Mitteilung", True),
    ]
    hits = index.search("Nachricht", locale="fr_CH").groups[0].hits
    assert [(hit.document.title, hit.secondary) for hit in hits] == [("Kontakt", True)]


def test_identical_pages_collapse_to_the_best_title_match():
    text = "Fehler melden, mitarbeiten oder Fragen stellen."
    index = SearchIndex(
        [
            _page("Community", text, duplicate_key="same"),
            _page("Kontakt", text, duplicate_key="same"),
        ]
    )
    assert _titles(index.search("Kontakt")) == ["Kontakt"]
    assert len(_titles(index.search("Fehler melden"))) == 1


def test_a_postcode_finds_its_municipality_by_address_share():
    index = SearchIndex(
        [
            _municipality("Affoltern am Albis", "ZH", 2, ("8910",), {"8910": 95}),
            _municipality("Obfelden", "ZH", 10, ("8910", "8912"), {"8910": 5}),
            _municipality("Zürich", "ZH", 261, ("8001", "8002")),
        ]
    )
    assert _titles(index.search("8910")) == ["Affoltern am Albis", "Obfelden"]
    assert _titles(index.search("891")) == ["Obfelden", "Affoltern am Albis"] or set(
        _titles(index.search("891"))
    ) == {"Obfelden", "Affoltern am Albis"}
    hit = index.search("8002").groups[0].hits[0]
    assert hit.document.title == "Zürich"
    assert [segment.text for segment in hit.snippet if segment.match] == ["8002"]


def test_a_municipality_name_needs_no_snippet():
    index = SearchIndex([_municipality("Zürich", "ZH", 261, ("8001",))])
    hit = index.search("Zuerich").groups[0].hits[0]
    assert hit.snippet == ()
    assert [(segment.text, segment.match) for segment in hit.title] == [
        ("Zürich", True)
    ]


def test_results_are_grouped_and_the_best_group_comes_first():
    index = SearchIndex(
        [
            _page("Baden und Umgebung", "Treffen in Baden."),
            _municipality("Baden", "AG", 4021, ("5400",)),
            _page("Glossar", "Baden ist eine Gemeinde.", kind="knowledge"),
        ]
    )
    result = index.search("Baden")
    assert [group.kind for group in result.groups] == [
        "municipality",
        "page",
        "knowledge",
    ]
    assert result.total == 3


def test_kind_filter_and_paging_keep_the_total():
    index = SearchIndex([_page(f"Seite {number}", "Solar") for number in range(12)])
    result = index.search("Solar", kinds={"page"}, limit=5, offset=10)
    assert result.group("page").total == 12
    assert len(result.group("page").hits) == 2


def test_snippet_comes_from_the_best_section_with_its_anchor():
    document = Document(
        kind="page",
        url="/leg-gruenden",
        title="LEG gründen",
        locale="de_CH",
        summary="Schritt für Schritt.",
        blocks=(
            Block(
                text="Die Gründung beginnt mit den Anschlüssen.",
                heading="Start",
                anchor="start",
            ),
            Block(
                text="Der Netzbetreiber liefert die Messdaten im SDAT-Format.",
                heading="Messdaten",
                anchor="daten",
            ),
        ),
    )
    hit = SearchIndex([document]).search("SDAT").groups[0].hits[0]
    assert (hit.anchor, hit.section) == ("daten", "Messdaten")
    assert "".join(segment.text for segment in hit.snippet).startswith(
        "Der Netzbetreiber"
    )
    long_text = "Wort " * 80 + "Zielbegriff " + "Rest " * 80
    hit = (
        SearchIndex([_page("Lang", long_text)]).search("Zielbegriff").groups[0].hits[0]
    )
    text = "".join(segment.text for segment in hit.snippet)
    assert text.startswith("… ") and text.endswith(" …") and len(text) < 200
    assert "Zielbegriff" in text


def test_did_you_mean_offers_the_known_spelling():
    index = SearchIndex(
        [
            _page("Photovoltaik", "Anlagen auf Dächern."),
            _municipality("Zürich", "ZH", 261),
        ]
    )
    result = index.search("Zürihc")
    assert _titles(result) == ["Zürich"]
    assert result.suggestion == "Zürich"
    assert index.search("Zürich").suggestion is None
    result = index.search("Photovoltaix Anlagne")
    assert result.suggestion == "Photovoltaik Anlagen"
    assert index.search("qqqqq").suggestion is None


def test_query_length_and_word_count_are_capped():
    index = SearchIndex([_page("Kosten")])
    result = index.search("kosten " + "x" * 500)
    assert len(result.query) == 100
    assert index.search("\x00\x07").total == 0


def test_typos_never_land_on_words_shorter_than_four_letters():
    index = SearchIndex([_page("Datum", "dat und mehr")])
    assert index.search("sdat").total == 0


def test_saint_spellings_find_each_other():
    index = SearchIndex(
        [
            _municipality("St. Gallen", "SG", 3203),
            _municipality("Saint-Imier", "BE", 443),
        ]
    )
    assert _titles(index.search("Sankt Gallen")) == ["St. Gallen"]
    assert _titles(index.search("St Imier")) == ["Saint-Imier"]


def test_a_word_that_matches_nothing_is_left_out_and_reported():
    index = SearchIndex([_page("Messdaten", "Der Netzbetreiber liefert Messdaten.")])
    result = index.search("Messdaten qqqq")
    assert _titles(result) == ["Messdaten"]
    assert result.ignored == ("qqqq",)
    assert index.search("Messdaten Netzbetreiber").ignored == ()
    # Words that each match something still have to match together.
    index = SearchIndex([_page("Messdaten"), _page("Abrechnung")])
    result = index.search("Messdaten Abrechnung")
    assert (result.total, result.ignored) == (0, ())


def test_longer_words_also_find_their_inflected_forms():
    index = SearchIndex(
        [
            _page("Was OpenLEG kostet"),
            _page("Rechner", "Kosten abschätzen", url="/rechner"),
        ]
    )
    assert _titles(index.search("Kosten")) == ["Was OpenLEG kostet", "Rechner"]


def test_a_suggestion_must_find_every_word():
    index = SearchIndex([_page("Rechnung"), _municipality("Siat", "GR", 3981)])
    # "sdat" is one typo away from "Siat", but no page has both words.
    result = index.search("Rechnung sdat")
    assert (result.total, result.suggestion) == (0, None)


def test_a_suggestion_leaves_out_words_that_match_nothing():
    index = SearchIndex([_page("Glossar", "Begriffe nachschlagen.")])
    result = index.search("Glosar qqqq")
    assert result.ignored == ("qqqq",)
    assert result.suggestion == "Glossar"
