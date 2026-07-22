# SPDX-License-Identifier: AGPL-3.0-or-later
"""PV-Nutzungsdaten laden.

Quelle: deterministisch gematchter Export aus dem dbm-leg-project (HSLU).
Numerator: installierte PV-Leistung aus BFE Elektrizitätsproduktionsanlagen.
Denominator: geschätztes Dachpotenzial aus BFE Sonnendach.
Kontext: BFS Regionalporträts (Einwohner, Dichte, Fläche).

Die Funktionen parse_* sind rein und testbar. Persistenz übernimmt database.
"""

import csv
import logging
from pathlib import Path
from typing import Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "data" / "public"
SNAPSHOT_CSV = DATA_DIR / "municipality_pv_current_snapshot.csv"
PANEL_CSV = DATA_DIR / "municipality_pv_panel_2016_2025.csv"

# Nationale, deterministische Matching-Quote (246'139 / 320'114 Anlagen).
PLANT_MATCH_RATE_PCT = 76.89

# Bezugsjahr des aktuellen Snapshots (municipality_pv_current_snapshot.csv,
# Spalte snapshot_year). Fortschritt nutzt stattdessen das Panel-Jahr.
SNAPSHOT_YEAR = 2026


def parse_snapshot_row(row: Dict) -> Optional[Dict]:
    """Eine Snapshot-CSV-Zeile in ein Profil-Upsert-Dict übersetzen."""
    bfs = _safe_int(row.get("bfs_nr"))
    if not bfs:
        return None
    return {
        "bfs_number": bfs,
        "name": (row.get("municipality_name") or "").strip(),
        "kanton": (row.get("canton_code") or "").strip().upper()[:2],
        "population": _safe_int(row.get("population_2019")),
        "density_per_km2": _round(row.get("density_per_km2_2019"), 2),
        "area_km2": _round(row.get("area_km2"), 2),
        "pv_score_pct": _round(row.get("pv_utilization_score_pct_current"), 2),
        "pv_estimated_potential_kw": _round(row.get("estimated_potential_kw"), 2),
        "pv_installed_kw": _round(row.get("current_total_kw"), 2),
        "pv_untapped_kw": _round(row.get("untapped_potential_kw_current"), 2),
        "pv_annual_potential_gwh": _round(row.get("annual_potential_gwh"), 2),
        "pv_snapshot_year": _safe_int(row.get("snapshot_year")),
        "pv_plant_match_rate": PLANT_MATCH_RATE_PCT,
    }


def parse_panel_row(row: Dict) -> Optional[Dict]:
    """Eine Panel-CSV-Zeile in ein Panel-Upsert-Dict übersetzen."""
    bfs = _safe_int(row.get("bfs_nr"))
    year = _safe_int(row.get("year"))
    if not bfs or not year:
        return None
    return {
        "bfs_number": bfs,
        "year": year,
        "added_kw": _round(row.get("pv_added_initial_kw"), 2),
        "added_plants": _safe_int(row.get("pv_added_plants")),
        "cumulative_kw": _round(row.get("pv_cumulative_initial_kw"), 2),
        "estimated_potential_kw": _round(row.get("estimated_potential_kw"), 2),
        "score_pct": _round(row.get("pv_utilization_score_pct"), 4),
        "untapped_kw": _round(row.get("untapped_potential_kw"), 2),
    }


def iter_csv(path: Path) -> Iterator[Dict]:
    with open(path, newline="", encoding="utf-8") as handle:
        yield from csv.DictReader(handle)


def load_snapshot(path: Path = SNAPSHOT_CSV) -> int:
    """Snapshot-CSV in municipality_profiles upserten. Gibt Zeilenzahl zurück."""
    import database as db

    count = 0
    for row in iter_csv(path):
        record = parse_snapshot_row(row)
        if record and db.upsert_municipality_pv(record):
            count += 1
    logger.info(f"[PV_DATA] Snapshot geladen: {count} Gemeinden")
    return count


def load_panel(path: Path = PANEL_CSV, batch_size: int = 2000) -> int:
    """Panel-CSV in municipality_pv_panel upserten. Gibt Zeilenzahl zurück."""
    import database as db

    total = 0
    batch: List[Dict] = []
    for row in iter_csv(path):
        record = parse_panel_row(row)
        if record:
            batch.append(record)
        if len(batch) >= batch_size:
            total += db.save_municipality_pv_panel(batch)
            batch = []
    if batch:
        total += db.save_municipality_pv_panel(batch)
    logger.info(f"[PV_DATA] Panel geladen: {total} Zeilen")
    return total


def refresh_pv_data() -> Dict:
    """Snapshot und Panel laden."""
    return {
        "snapshot_rows": load_snapshot(),
        "panel_rows": load_panel(),
        "plant_match_rate_pct": PLANT_MATCH_RATE_PCT,
    }


def _safe_int(val) -> Optional[int]:
    if val is None or val == "":
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def _safe_float(val) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        return float(str(val).replace(",", "."))
    except (ValueError, TypeError):
        return None


def _round(val, digits: int) -> Optional[float]:
    parsed = _safe_float(val)
    return round(parsed, digits) if parsed is not None else None
