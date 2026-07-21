# SPDX-License-Identifier: AGPL-3.0-or-later
"""Distribution ladder + honest revenue model on /pricing (Program 9 W7).

Presents the ways to run OpenLEG from DIY self-host to managed options, and
states the revenue model honestly: sell convenience, hardware and operation,
never citizen data. Managed offerings that do not exist yet are marked as
planned, not advertised as purchasable today.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRICING = ROOT / "templates" / "pricing.html"


def _html():
    return PRICING.read_text(encoding="utf-8")


def test_shows_run_options_ladder():
    html = _html()
    assert "Selbst betreiben" in html
    assert "Gehostet" in html
    assert "OpenLEG-Box" in html
    assert "OpenLEG-Netzwerk" in html


def test_managed_options_marked_as_planned():
    # Honesty: do not advertise a box / VPS / network you can buy today.
    html = _html().lower()
    assert "vorbereitung" in html or "anfrage" in html


def test_revenue_model_never_sells_data():
    html = _html()
    assert "niemals" in html or "kein Datenverkauf" in html
    assert "Komfort" in html or "Hardware" in html


def test_links_to_self_host():
    assert "/self-host" in _html()


def test_swiss_hochdeutsch_rules():
    html = _html()
    assert "ß" not in html
    assert "—" not in html  # em dash
    assert "–" not in html  # en dash


def test_keeps_free_positioning():
    # An existing route test asserts the page still contains "Kostenlos".
    assert "Kostenlos" in _html()
