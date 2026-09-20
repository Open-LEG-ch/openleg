# SPDX-License-Identifier: AGPL-3.0-or-later
"""Repository-level German copy compliance sweep (#523).

The engineering contract requires Schweizer Hochdeutsch in user-facing copy:
real umlauts, "ss" instead of "ß", active voice, no en or em dashes. The
invoice templates pin their own strings; this sweep generalises the check
across every template in the repository, including system emails.

The scan extracts text nodes only. Script and style blocks, tags and their
attributes, comments, and Jinja expressions are stripped, so code
identifiers, attributes, and URLs that must stay ASCII are never scanned.
Words with a literal "ae", "oe", or "ue" are allowed only when they carry a
stem on the documented allow-list; everything else is treated as a wrongly
spelled umlaut and fails the sweep.
"""

import re
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

# Stems whose literal "ae/oe/ue" is correct German or an accepted loanword,
# with the justification. A word passes if its lowercase form contains one
# of these stems, which also admits compounds such as Speicherdauer and
# Datenquellen.
ALLOWED_STEMS = {
    "quell": "Quelle family: Quelle, Quellen, Quellcode, Quelldokumente, "
    "Datenquellen, Rechtsquellen, Quelloffene (ue after l, not an umlaut)",
    "neu": "neue, Neue, neuen (diphthong eu, not an umlaut)",
    "aktuell": "aktuell family (double l, not an umlaut)",
    "dauer": "Dauer, Speicherdauer (au diphthong)",
    "schau": "Schauen, schauen (au diphthong)",
    "bau": "bauen, einbauen (au diphthong)",
    "manuell": "manuell family (double l)",
    "erneuer": "erneuerbare(n) (eu diphthong)",
    "neuenburg": "Neuenburg, the Canton (proper noun)",
    "steuer": "Mehrwertsteuer, steuert (eu diphthong)",
    "individuell": "individuellen (double l)",
    "vertrau": "Vertrauen (au diphthong)",
    "unbequem": "unbequeme (ue after q, not an umlaut)",
    "value": "Value-Gap, English loanword kept in English",
    "request": "Request, English technical term",
    "zuerst": "zuerst (ue after z, not an umlaut)",
    "virtuell": "virtueller Zusammenschluss zum Eigenverbrauch, vZEV "
    "(ue after v, not an umlaut)",
}


def _visible_text(template: str) -> str:
    text = re.sub(
        r"<script\b.*?</script[^>]*>", " ", template, flags=re.DOTALL | re.IGNORECASE
    )
    text = re.sub(
        r"<style\b.*?</style[^>]*>", " ", text, flags=re.DOTALL | re.IGNORECASE
    )
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\{\{.*?\}\}", " ", text, flags=re.DOTALL)
    text = re.sub(r"\{%.*?%\}", " ", text, flags=re.DOTALL)
    return text


def _violations(text: str) -> list[str]:
    found = []
    if "ß" in text:
        found.append("contains ß (the contract requires ss)")
    for match in re.finditer(r"–|—", text):
        context = text[max(0, match.start() - 30) : match.end() + 30]
        found.append(f"en/em dash in ...{context.strip()!r}...")
    for word in set(re.findall(r"[A-Za-zÄÖÜäöü_-]*[aouAOU]e[A-Za-zÄÖÜäöü_-]*", text)):
        lowered = word.lower()
        if not any(stem in lowered for stem in ALLOWED_STEMS):
            found.append(f"possible false ae/oe/ue spelling: {word!r}")
    return found


def _template_files():
    return sorted(TEMPLATES_DIR.rglob("*.html"))


def test_every_template_carries_compliant_german_copy():
    offenders = []
    for path in _template_files():
        found = _violations(_visible_text(path.read_text(encoding="utf-8")))
        for violation in found:
            offenders.append(f"{path.relative_to(TEMPLATES_DIR.parent)}: {violation}")
    assert not offenders, "German copy violations found:\n" + "\n".join(offenders)


def test_allow_list_entries_are_real_and_justified():
    """Every allow-list stem must actually occur, so the list cannot rot."""
    corpus = "\n".join(
        _visible_text(path.read_text(encoding="utf-8")) for path in _template_files()
    )
    words = set(re.findall(r"[A-Za-zÄÖÜäöü_-]+", corpus.lower()))
    for stem, justification in ALLOWED_STEMS.items():
        assert justification, f"stem {stem!r} needs a stated reason"
        assert any(word.startswith(stem) for word in words), (
            f"stem {stem!r} appears in no template; drop the entry"
        )
