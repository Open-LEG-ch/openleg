# SPDX-License-Identifier: AGPL-3.0-or-later
"""SVG-Badges und Social-Cards für die Solarnutzungs-Rangliste.

Reine Funktionen, die SVG-Strings bauen. Kein PNG-Rendering, damit keine
nativen Abhängigkeiten nötig sind. Für Plattformen ohne SVG-Vorschau lässt
sich später ein PNG-Export ergänzen.
"""

from xml.sax.saxutils import escape

BRAND = "#4f46e5"
INK = "#0f172a"
MUTED = "#475569"


def _score_text(score: float | None) -> str:
    return f"{score:.0f}%" if score is not None else "n/a"


def badge_svg(name: str, score: float | None, rank: int | None) -> str:
    """Kompaktes Einbett-Badge: Solarnutzung und nationaler Rang."""
    safe_name = escape((name or "").strip())[:28]
    rank_line = f"Rang {rank} CH" if rank else "OpenLEG"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="320" height="84" viewBox="0 0 320 84" role="img" aria-label="Solarnutzung {safe_name}">
  <rect width="320" height="84" rx="10" fill="#ffffff" stroke="#e2e8f0"/>
  <rect width="8" height="84" rx="0" fill="{BRAND}"/>
  <text x="22" y="26" font-family="Arial, sans-serif" font-size="13" fill="{MUTED}">Solarnutzung</text>
  <text x="22" y="56" font-family="Arial, sans-serif" font-size="30" font-weight="700" fill="{INK}">{_score_text(score)}</text>
  <text x="150" y="26" font-family="Arial, sans-serif" font-size="13" fill="{MUTED}">{escape(safe_name)}</text>
  <text x="150" y="50" font-family="Arial, sans-serif" font-size="15" font-weight="600" fill="{BRAND}">{rank_line}</text>
  <text x="150" y="70" font-family="Arial, sans-serif" font-size="11" fill="{MUTED}">openleg.ch</text>
</svg>"""


def og_card_svg(
    name: str,
    kanton: str,
    score: float | None,
    rank: int | None,
    untapped_kw: float | None,
) -> str:
    """Social-Card 1200x630 für og:image."""
    safe_name = escape((name or "").strip())[:40]
    safe_kanton = escape((kanton or "").strip())
    rank_line = f"Rang {rank} in der Schweiz" if rank else "Schweizer Gemeinde"
    untapped_line = (
        f"{int(untapped_kw):,} kW ungenutztes Dachpotenzial".replace(",", "'")
        if untapped_kw is not None
        else "Offene Daten von BFE und BFS"
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630" role="img" aria-label="Solarnutzung {safe_name}">
  <rect width="1200" height="630" fill="#f6f4ef"/>
  <rect width="1200" height="16" fill="{BRAND}"/>
  <text x="80" y="120" font-family="Arial, sans-serif" font-size="34" fill="{MUTED}">OpenLEG Solarnutzungs-Rangliste</text>
  <text x="80" y="230" font-family="Arial, sans-serif" font-size="84" font-weight="700" fill="{INK}">{safe_name}</text>
  <text x="80" y="290" font-family="Arial, sans-serif" font-size="34" fill="{MUTED}">Kanton {safe_kanton} · {rank_line}</text>
  <text x="80" y="450" font-family="Arial, sans-serif" font-size="48" fill="{MUTED}">Solarnutzung</text>
  <text x="80" y="540" font-family="Arial, sans-serif" font-size="120" font-weight="700" fill="{BRAND}">{_score_text(score)}</text>
  <text x="80" y="590" font-family="Arial, sans-serif" font-size="28" fill="{MUTED}">{untapped_line} · openleg.ch</text>
</svg>"""
