"""Catalog sync and drift checks on isolated production source trees."""

import json

import pytest
from babel.messages.pofile import read_po

from scripts import translations as workflow


@pytest.fixture
def tree(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow, "ROOT", tmp_path)
    monkeypatch.setattr(workflow, "TRANSLATIONS", tmp_path / "translations")
    (tmp_path / "templates").mkdir()
    (tmp_path / "babel.cfg").write_text(
        "[python: **.py]\n[jinja2: templates/**.html]\n", encoding="utf-8"
    )
    (tmp_path / "templates/page.html").write_text('{{ _("Hallo") }}', encoding="utf-8")
    # Create explicitly authored translations, independent of sync.
    for locale in workflow.LOCALES:
        path = tmp_path / f"translations/{locale}/LC_MESSAGES/messages.po"
        path.parent.mkdir(parents=True)
        path.write_text(
            'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n\n'
            "#. Draft checked against source; native language and domain review pending.\n"
            'msgid "Hallo"\nmsgstr "Bonjour"\n',
            encoding="utf-8",
        )
    return tmp_path


def snapshot(root):
    return {
        str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }


def test_sync_is_repeatable_and_check_writes_nothing(tree):
    workflow.run("sync")
    before = snapshot(tree)
    workflow.run("sync")
    workflow.run("check")
    assert snapshot(tree) == before
    assert b"Nur Prototyp" not in (tree / "translations/messages.pot").read_bytes()
    assert (
        b"Draft checked against source"
        in (tree / "translations/en/LC_MESSAGES/messages.po").read_bytes()
    )


@pytest.mark.parametrize(
    "artifact",
    [
        "pot",
        "po-missing",
        "po-extra",
        "po-obsolete",
        "placeholder",
        "fuzzy",
        "empty",
        "mo",
        "source-digest",
        "locale-digest",
        "svg",
    ],
)
def test_independent_drift_is_read_only(tree, artifact):
    assets = tree / "templates/assets"
    assets.mkdir()
    (assets / "example.svg").write_text("<svg/>", encoding="utf-8")
    workflow.run("sync")
    po = tree / "translations/en/LC_MESSAGES/messages.po"
    state = tree / "translations/review-status.json"
    if artifact == "pot":
        (tree / "templates/page.html").write_text('{{ _("Neu") }}')
    elif artifact == "po-missing":
        po.write_text(po.read_text().split("#. Draft")[0])
    elif artifact == "po-extra":
        po.write_text(po.read_text() + '\nmsgid "Obsolet"\nmsgstr "Old"\n')
    elif artifact == "po-obsolete":
        po.write_text(po.read_text() + '\n#~ msgid "Obsolet"\n#~ msgstr "Old"\n')
    elif artifact == "placeholder":
        po.write_text(po.read_text().replace('msgstr "Bonjour"', 'msgstr "%(wrong)s"'))
    elif artifact in ("fuzzy", "empty"):
        po.write_text(
            po.read_text().replace('msgid "Hallo"', '#, fuzzy\nmsgid "Hallo"')
            if artifact == "fuzzy"
            else po.read_text().replace('msgstr "Bonjour"', 'msgstr ""')
        )
    elif artifact == "mo":
        po.with_suffix(".mo").write_bytes(b"broken")
    elif artifact == "svg":
        (tree / "static/images/en/example.svg").write_text("<svg>old</svg>")
    else:
        data = json.loads(state.read_text())
        if artifact == "source-digest":
            data["source_digest"] = "old"
        else:
            data["locales"]["en"]["catalog_digest"] = "old"
        state.write_text(json.dumps(data))
    before = snapshot(tree)
    with pytest.raises(ValueError):
        workflow.run("check")
    assert snapshot(tree) == before


def test_sync_prunes_and_requires_explicit_foreign_authoring(tree):
    workflow.run("sync")
    (tree / "templates/page.html").write_text('{{ _("Neu") }}')
    with pytest.raises(ValueError, match="Untranslated"):
        workflow.run("sync")
    for locale in workflow.LOCALES:
        with (tree / f"translations/{locale}/LC_MESSAGES/messages.po").open(
            "rb"
        ) as handle:
            catalog = read_po(handle, locale=locale)
        assert catalog.get("Hallo") is None
        assert catalog.get("Neu").string == ("Neu" if locale == "de_CH" else "")


def test_changed_revision_invalidates_approval_and_keeps_evidence(tree):
    workflow.run("sync")
    state = tree / "translations/review-status.json"
    data = json.loads(state.read_text())
    data.update(native_approval=True, legal_approval=True, status="approved")
    data["locales"]["en"].update(
        native_review_status="approved",
        reviewer="reviewer",
        evidence_reference="review/1",
    )
    state.write_text(json.dumps(data))
    workflow.run("sync")
    assert json.loads(state.read_text()) == data
    po = tree / "translations/en/LC_MESSAGES/messages.po"
    po.write_text(po.read_text().replace("Bonjour", "Hello"))
    workflow.run("sync")
    changed = json.loads(state.read_text())
    assert changed["native_approval"] is False and changed["legal_approval"] is False
    assert changed["locales"]["en"]["native_review_status"] == "pending"
    assert (
        changed["superseded_reviews"][0]["locales"]["en"]["evidence_reference"]
        == "review/1"
    )


def test_sync_preserves_plural_forms_and_contexts_and_prunes_obsolete_blocks(tree):
    (tree / "templates/page.html").write_text(
        '{{ ngettext("Ein Haus", "%(count)s Häuser", 2) }}'
        '{{ pgettext("navigation", "Offen") }}'
        '{{ pgettext("action", "Offen") }}',
        encoding="utf-8",
    )
    for locale in workflow.LOCALES:
        path = tree / f"translations/{locale}/LC_MESSAGES/messages.po"
        path.write_text(
            path.read_text() + "\n"
            'msgid "Ein Haus"\nmsgid_plural "%(count)s Häuser"\n'
            'msgstr[0] "One house"\nmsgstr[1] "%(count)s houses"\n\n'
            'msgctxt "navigation"\nmsgid "Offen"\nmsgstr "Open"\n\n'
            'msgctxt "action"\nmsgid "Offen"\nmsgstr "Open action"\n\n'
            '#~ msgid "Entfernt"\n#~ msgstr "Removed"\n',
            encoding="utf-8",
        )
    workflow.run("sync")
    workflow.run("check")
    with (tree / "translations/en/LC_MESSAGES/messages.po").open("rb") as stream:
        catalog = read_po(stream, locale="en")
    assert catalog.get(("Ein Haus", "%(count)s Häuser")).string == (
        "One house",
        "%(count)s houses",
    )
    assert catalog.get("Offen", context="navigation").string == "Open"
    assert catalog.get("Offen", context="action").string == "Open action"
    assert not catalog.obsolete
    before = snapshot(tree)
    workflow.run("sync")
    assert snapshot(tree) == before


def test_extraction_covers_production_formats(tree):
    (tree / "messages.py").write_text('_("Python Nachricht")', encoding="utf-8")
    (tree / "templates/email.txt").write_text(
        '{{ _("Text Nachricht") }}', encoding="utf-8"
    )
    (tree / "templates/picture.svg").write_text(
        '<svg>{{ _("SVG Nachricht") }}</svg>', encoding="utf-8"
    )
    config = tree / "babel.cfg"
    config.write_text(
        config.read_text() + "[jinja2: templates/**.txt]\n[jinja2: templates/**.svg]\n"
    )
    catalog = workflow.extract(tree / "temporary.pot")
    for text in ("Hallo", "Python Nachricht", "Text Nachricht", "SVG Nachricht"):
        assert catalog.get(text) is not None


@pytest.mark.parametrize(
    "locale,label",
    [
        ("de_CH", "Unternehmen"),
        ("fr_CH", "Entreprise"),
        ("en", "Enterprise"),
        ("it_CH", "Impresa"),
    ],
)
def test_sync_preserves_authored_labels_in_po_and_compiled_catalog(tree, locale, label):
    from babel.support import Translations

    (tree / "templates/page.html").write_text('{{ _("enterprise") }}', encoding="utf-8")
    for language in workflow.LOCALES:
        path = tree / f"translations/{language}/LC_MESSAGES/messages.po"
        path.write_text(
            path.read_text()
            + f'\n# Human translation.\nmsgid "enterprise"\nmsgstr "{label}"\n',
            encoding="utf-8",
        )
    path = tree / f"translations/{locale}/LC_MESSAGES/messages.po"
    block = path.read_text().split("# Human translation.")[1]
    workflow.run("sync")
    workflow.run("check")
    assert path.read_text().split("# Human translation.")[1] == block
    assert (
        Translations.load(tree / "translations", locales=[locale]).gettext("enterprise")
        == label
    )
