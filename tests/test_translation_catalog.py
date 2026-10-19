# SPDX-License-Identifier: AGPL-3.0-or-later
"""Translation validation fails on missing text, stale builds or placeholders."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from babel.messages.catalog import Catalog
from babel.messages.mofile import write_mo
from babel.messages.pofile import write_po

from scripts.translations import normalize_flags, validate


@pytest.mark.parametrize("locale", ["de_CH", "fr_CH"])
def test_plural_placeholders_compile_and_render_per_source_form(
    tmp_path, monkeypatch, locale
):
    from flask import Flask, render_template_string

    from i18n import init_app
    from scripts import translations

    identifiers = ("Ein Eintrag", "%(count)s Einträge")
    strings = ("Une entrée", "%(count)s entrées") if locale == "fr_CH" else identifiers
    source = Catalog()
    source.add(identifiers)
    catalog = Catalog(locale=locale)
    catalog.add(identifiers, strings)
    normalize_flags(catalog)
    validate(catalog, source)
    directory = tmp_path / "translations" / locale / "LC_MESSAGES"
    directory.mkdir(parents=True)
    with (directory / "messages.po").open("wb") as handle:
        write_po(handle, catalog)
    monkeypatch.setattr(translations, "ROOT", tmp_path)
    monkeypatch.setattr(translations, "TRANSLATIONS", tmp_path / "translations")
    monkeypatch.setattr(translations, "extract", lambda path: source)
    for command in ("compile", "check"):
        monkeypatch.setattr(sys, "argv", ["translations.py", command])
        translations.main()

    web = Flask(__name__)
    web.config.update(
        SECRET_KEY="synthetic-plural-test",
        BABEL_DEFAULT_LOCALE=locale,
        BABEL_TRANSLATION_DIRECTORIES=str(tmp_path / "translations"),
    )
    init_app(web)
    for count in (0, 1, 2, 5):
        with web.test_request_context("/?lang=" + locale):
            rendered = render_template_string(
                "{{ ngettext(singular, plural, count, count=count) }}",
                singular=identifiers[0],
                plural=identifiers[1],
                count=count,
            )
        singular = count in (0, 1) if locale == "fr_CH" else count == 1
        assert rendered == (strings[0] if singular else strings[1] % {"count": count})


@pytest.mark.parametrize(
    "strings",
    [
        ("Une entrée", "Des entrées"),
        ("Une entrée", "%(wrong)s entrées"),
        ("Une entrée %(unexpected)s", "%(count)s entrées"),
        ("Une entrée", "%(count)d entrées"),
    ],
)
def test_plural_forms_reject_missing_unknown_and_changed_placeholders(strings):
    identifiers = ("Ein Eintrag", "%(count)s Einträge")
    source = Catalog()
    source.add(identifiers)
    catalog = Catalog(locale="fr_CH")
    catalog.add(identifiers, strings)
    normalize_flags(catalog)
    with pytest.raises(ValueError, match="Named placeholder mismatch"):
        validate(catalog, source)


def test_plural_singular_form_cannot_drop_a_required_placeholder():
    identifiers = ("Eintrag von %(name)s", "%(count)s Einträge von %(name)s")
    source = Catalog()
    source.add(identifiers)
    catalog = Catalog(locale="fr_CH")
    catalog.add(identifiers, ("Une entrée", "%(count)s entrées de %(name)s"))
    normalize_flags(catalog)
    with pytest.raises(ValueError, match="Named placeholder mismatch"):
        validate(catalog, source)


@pytest.mark.parametrize("last_form", ["%(wrong)s other items", "Other items"])
def test_every_additional_plural_form_is_validated(last_form):
    identifiers = ("Ein Eintrag", "%(count)s Einträge")
    source = Catalog()
    source.add(identifiers)
    # The validator is generic even though runtime selection currently supports
    # only de_CH and fr_CH. Catalogs can contain more than two plural forms.
    catalog = Catalog(locale="ru")
    message = catalog.add(
        identifiers, ("One item", "%(count)s items", "%(count)s other items")
    )
    normalize_flags(catalog)
    validate(catalog, source)
    message.string = ("One item", "%(count)s items", last_form)
    with pytest.raises(ValueError, match="Named placeholder mismatch"):
        validate(catalog, source)


def test_localized_svg_bytes_and_freshness_are_utf8_under_ascii_defaults(tmp_path):
    code = r"""
import locale
import sys
from pathlib import Path
from babel.messages.catalog import Catalog
from babel.messages.mofile import write_mo
from scripts import translations

assert locale.getencoding() in ("ANSI_X3.4-1968", "US-ASCII", "ascii"), locale.getencoding()
root = Path(sys.argv[1])
source = root / "templates/assets/example.svg"
source.parent.mkdir(parents=True)
template = '<svg xmlns="http://www.w3.org/2000/svg"><text>{{ _("Gr\u00fcne Energie") }}</text></svg>\n'
source.write_text(template, encoding="utf-8")
for language, label in [("de_CH", "Gr\u00fcne Energie"), ("fr_CH", "\u00c9nergie renouvelable")]:
    catalog = Catalog(locale=language)
    catalog.add("Gr\u00fcne Energie", label)
    mo = root / "translations" / language / "LC_MESSAGES/messages.mo"
    mo.parent.mkdir(parents=True)
    with mo.open("wb") as handle:
        write_mo(handle, catalog)
translations.ROOT = root
translations.TRANSLATIONS = root / "translations"
translations.build_svg_assets(check=False)
for language, label in [("de-CH", "Gr\u00fcne Energie"), ("fr-CH", "\u00c9nergie renouvelable")]:
    target = root / "static/images" / ("" if language == "de-CH" else language) / "example.svg"
    expected = '<svg xmlns="http://www.w3.org/2000/svg"><text>' + label + '</text></svg>\n'
    assert target.read_bytes() == expected.encode("utf-8")
translations.build_svg_assets(check=True)
target = root / "static/images/fr-CH/example.svg"
target.write_text("<svg/>", encoding="utf-8")
try:
    translations.build_svg_assets(check=True)
except ValueError as error:
    assert "Localized asset is stale" in str(error)
else:
    raise AssertionError("stale localized SVG accepted")
"""
    environment = os.environ.copy()
    environment.update(LC_ALL="C", PYTHONUTF8="0", PYTHONCOERCECLOCALE="0")
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_shipped_catalog_matches_current_sources_and_compiled_output():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/translations.py", "check"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_catalog_validation_rejects_missing_source_messages_and_fuzzy_text():
    source = Catalog()
    source.add("Neue Nachricht")
    french = Catalog(locale="fr_CH")
    with pytest.raises(ValueError, match="Missing source message"):
        validate(french, source)
    french.add("Neue Nachricht", "Nouveau message", flags={"fuzzy"})
    with pytest.raises(ValueError, match="fuzzy"):
        validate(french, source)


def test_catalog_validation_keeps_named_placeholder_checks():
    source = Catalog()
    source.add("Hallo %(name)s")
    french = Catalog(locale="fr_CH")
    french.add("Hallo %(name)s", "Bonjour %(wrong)s")
    normalize_flags(french)
    with pytest.raises(ValueError, match="name|wrong"):
        validate(french, source)


def test_literal_percent_prose_is_not_mistaken_for_python_interpolation():
    source = Catalog()
    source.add("40% ohne Transformation")
    french = Catalog(locale="fr_CH")
    french.add("40% ohne Transformation", "40% sans transformation")
    normalize_flags(french)
    validate(french, source)


def test_translation_cannot_introduce_a_placeholder_into_plain_source():
    source = Catalog()
    source.add("Speichern")
    french = Catalog(locale="fr_CH")
    french.add("Speichern", "Enregistrer %(unexpected)s")
    normalize_flags(french)
    with pytest.raises(ValueError, match="unexpected|placeholder"):
        validate(french, source)


def test_compiled_plural_rule_must_match_the_po(tmp_path, monkeypatch):
    from scripts import translations

    source = Catalog()
    source.add(("Ein Haushalt", "Haushalte"))
    french = Catalog(locale="fr_CH")
    french.add(("Ein Haushalt", "Haushalte"), ("Un ménage", "Des ménages"))
    directory = tmp_path / "translations/fr_CH/LC_MESSAGES"
    directory.mkdir(parents=True)
    with (directory / "messages.po").open("wb") as handle:
        write_po(handle, french)
    french._plural_expr = "(n != 1)"
    with (directory / "messages.mo").open("wb") as handle:
        write_mo(handle, french)
    monkeypatch.setattr(translations, "ROOT", tmp_path)
    monkeypatch.setattr(translations, "TRANSLATIONS", tmp_path / "translations")
    monkeypatch.setattr(translations, "extract", lambda path: source)
    monkeypatch.setattr(sys, "argv", ["translations.py", "check"])
    with pytest.raises(ValueError, match="plural|Plural"):
        translations.main()


def test_contextual_messages_cannot_be_satisfied_by_another_context():
    source = Catalog()
    source.add("Open", context="navigation")
    french = Catalog(locale="fr_CH")
    french.add("Open", "Ouvrir", context="document-action")
    with pytest.raises(ValueError, match="navigation"):
        validate(french, source)


def test_compiled_contextual_messages_are_checked_with_their_context(
    tmp_path, monkeypatch, capsys
):
    from scripts import translations

    source = Catalog()
    source.add("Open", context="navigation")
    source.add("Open", context="document-action")
    french = Catalog(locale="fr_CH")
    french.add("Open", "Ouvert", context="navigation")
    french.add("Open", "Ouvrir", context="document-action")
    directory = tmp_path / "translations/fr_CH/LC_MESSAGES"
    directory.mkdir(parents=True)
    with (directory / "messages.po").open("wb") as handle:
        write_po(handle, french)
    with (directory / "messages.mo").open("wb") as handle:
        write_mo(handle, french)
    monkeypatch.setattr(translations, "ROOT", tmp_path)
    monkeypatch.setattr(translations, "TRANSLATIONS", tmp_path / "translations")
    monkeypatch.setattr(translations, "extract", lambda path: source)
    monkeypatch.setattr(sys, "argv", ["translations.py", "check"])
    translations.main()
    assert "2 translated messages, OK" in capsys.readouterr().out
    french.get("Open", context="navigation").string = "Autre"
    with (directory / "messages.po").open("wb") as handle:
        write_po(handle, french)
    with pytest.raises(ValueError, match="stale"):
        translations.main()
