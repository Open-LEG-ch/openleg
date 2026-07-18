# SPDX-License-Identifier: AGPL-3.0-or-later
"""Municipality profile pages report the local LEG picture.

Phase 7 of docs/leg-registry.md: a Gemeinde page shows published entries
from the open registry for that municipality, funnels into /leg-check,
and the municipalities pathway page links the registry.
"""

import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(PROJECT_ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


def test_profil_route_loads_registry_entries():
    source = _read("municipality.py")
    assert "list_registry_entries" in source


def test_profil_template_has_leg_section():
    html = _read("templates", "gemeinde", "profil.html")
    assert "Lokale Elektrizitätsgemeinschaften" in html
    assert "leg_entries" in html
    assert 'href="/leg-check?q=' in html
    assert 'href="/leg-verzeichnis/eintragen"' in html


def test_fuer_gemeinden_links_registry():
    html = _read("templates", "fuer_gemeinden.html")
    assert 'href="/leg-verzeichnis"' in html
