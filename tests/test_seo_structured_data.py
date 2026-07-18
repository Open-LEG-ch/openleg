# SPDX-License-Identifier: AGPL-3.0-or-later
"""SEO structured data: FAQPage on /leg-gruenden, ItemList on the registry.

Phase 8 of docs/leg-registry.md. The FAQ answers must mirror the visible
<details> content; the registry list emits an ItemList over published
entries only (whatever the route already filtered).
"""

import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(PROJECT_ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


def test_leg_gruenden_has_faq_jsonld():
    html = _read("templates", "leg_gruenden.html")
    assert '"@type": "FAQPage"' in html
    assert '"@type": "Question"' in html
    assert '"@type": "Answer"' in html


def test_leg_verzeichnis_liste_has_itemlist_jsonld():
    html = _read("templates", "leg_verzeichnis", "liste.html")
    assert '"@type": "ItemList"' in html
    assert "itemListElement" in html
