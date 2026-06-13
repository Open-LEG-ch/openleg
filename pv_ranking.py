# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ranglisten-Logik für die Gemeinde-Solarnutzung.

Reine Funktionen: Ligen (Kanton, Grösse, Dichte), Quartils-Ranking,
Verbesserungsziel, Vorbild-Eignung. Keine DB-Zugriffe.
"""

from math import floor
from typing import Dict, List, Optional, Tuple

# Grössenbänder nach Einwohnerzahl
SIZE_BANDS = (("small", 5000), ("medium", 20000), ("large", 100000))
SIZE_XL = "xl"

# Dichtebänder nach Einwohner pro km2
DENSITY_BANDS = (("low", 250), ("mid", 1000), ("high", 3000))
DENSITY_VERY_HIGH = "very_high"

# Durchschnittliche Dachanlage in kWp (für die Dächer-Schätzung)
AVG_ROOF_KWP = 10.0

# Eine Gemeinde darf nur dann als Vorbild zitiert werden, wenn ihr
# Jahrespotenzial gross genug ist (Dorf-Artefakte ausschliessen).
LEADER_MIN_GWH = 5.0

TOP_QUARTILE = 1


def size_band(population: Optional[float]) -> Optional[str]:
    if population is None:
        return None
    for label, ceiling in SIZE_BANDS:
        if population < ceiling:
            return label
    return SIZE_XL


def density_band(density: Optional[float]) -> Optional[str]:
    if density is None:
        return None
    for label, ceiling in DENSITY_BANDS:
        if density < ceiling:
            return label
    return DENSITY_VERY_HIGH


def capped_score(score: Optional[float]) -> Tuple[Optional[float], bool]:
    """Score auf 100 deckeln. Zweiter Wert: Schätzung übertroffen."""
    if score is None:
        return None, False
    if score > 100:
        return 100.0, True
    return round(score, 1), False


def is_leader_eligible(row: Dict) -> bool:
    return float(row.get("pv_annual_potential_gwh") or 0) >= LEADER_MIN_GWH


def _score_key(row: Dict) -> float:
    score = row.get("pv_score_pct")
    return float(score) if score is not None else -1.0


def assign_ranks(rows: List[Dict]) -> List[Dict]:
    """Nach Score absteigend sortieren, Rang und Quartil ergänzen.

    Quartil 1 = bestes Viertel. Gibt neue Dicts zurück.
    """
    ordered = sorted(rows, key=_score_key, reverse=True)
    total = len(ordered)
    enriched = []
    for index, row in enumerate(ordered):
        quartile = min(4, floor(index * 4 / total) + 1) if total else 1
        enriched.append(
            {
                **row,
                "rank": index + 1,
                "rank_total": total,
                "quartile": quartile,
                "recommendation": recommendation(quartile),
            }
        )
    return enriched


def recommendation(quartile: int) -> str:
    if quartile == 1:
        return "vorbild"
    if quartile == 4:
        return "grosse_chance"
    return "auf_kurs"


def top_quartile_threshold(rows: List[Dict]) -> Optional[float]:
    """Kleinster Score im besten Viertel: die Schwelle zum Aufstieg."""
    ranked = assign_ranks(rows)
    top = [r["pv_score_pct"] for r in ranked if r["quartile"] == TOP_QUARTILE]
    top = [s for s in top if s is not None]
    return min(top) if top else None


def improvement_target(row: Dict, threshold_score: Optional[float]) -> Optional[Dict]:
    """Wie viel kW fehlen, um das beste Viertel der Liga zu erreichen."""
    if threshold_score is None:
        return None
    potential = row.get("pv_estimated_potential_kw")
    installed = row.get("pv_installed_kw")
    if potential is None or installed is None:
        return None
    target_kw = threshold_score / 100.0 * float(potential)
    needed_kw = max(0.0, target_kw - float(installed))
    return {
        "target_score": round(threshold_score, 1),
        "needed_kw": round(needed_kw, 1),
        "roofs": round(needed_kw / AVG_ROOF_KWP),
    }


def filter_league(
    rows: List[Dict],
    kanton: Optional[str] = None,
    size: Optional[str] = None,
    density: Optional[str] = None,
) -> List[Dict]:
    """Gemeinden auf eine Liga eingrenzen."""
    result = rows
    if kanton:
        target = kanton.strip().upper()
        result = [r for r in result if (r.get("kanton") or "").upper() == target]
    if size:
        result = [r for r in result if size_band(r.get("population")) == size]
    if density:
        result = [
            r for r in result if density_band(r.get("density_per_km2")) == density
        ]
    return result
