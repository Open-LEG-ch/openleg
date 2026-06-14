# SPDX-License-Identifier: AGPL-3.0-or-later
"""PV-Nutzungs- und Rangliste-Speicher (municipality_profiles PV columns + panel).

Repository module for the PV/ranking domain. The connection seam is resolved
via ``database.get_connection`` at call time so existing tests that
``monkeypatch.setattr(database, "get_connection", ...)`` keep working unchanged
and ``database`` can re-export these functions for legacy callers.
"""

import logging
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)


def _get_connection():
    import database

    return database.get_connection()


def upsert_municipality_pv(profile: Dict) -> bool:
    """Upsert PV-Nutzungs-Kennzahlen auf ein Gemeindeprofil.

    Setzt nur PV- und Geo-Spalten plus Name/Kanton/Einwohner. ElCom-, Solar-
    und Energiewende-Felder bleiben unberührt, damit der Massenimport reichere
    bestehende Profile nicht überschreibt.
    """
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO municipality_profiles (
                        bfs_number, name, kanton, population,
                        density_per_km2, area_km2,
                        pv_score_pct, pv_estimated_potential_kw, pv_installed_kw,
                        pv_untapped_kw, pv_annual_potential_gwh, pv_snapshot_year,
                        pv_plant_match_rate)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (bfs_number) DO UPDATE SET
                        name = EXCLUDED.name, kanton = EXCLUDED.kanton,
                        population = EXCLUDED.population,
                        density_per_km2 = EXCLUDED.density_per_km2,
                        area_km2 = EXCLUDED.area_km2,
                        pv_score_pct = EXCLUDED.pv_score_pct,
                        pv_estimated_potential_kw = EXCLUDED.pv_estimated_potential_kw,
                        pv_installed_kw = EXCLUDED.pv_installed_kw,
                        pv_untapped_kw = EXCLUDED.pv_untapped_kw,
                        pv_annual_potential_gwh = EXCLUDED.pv_annual_potential_gwh,
                        pv_snapshot_year = EXCLUDED.pv_snapshot_year,
                        pv_plant_match_rate = EXCLUDED.pv_plant_match_rate,
                        updated_at = CURRENT_TIMESTAMP
                """,
                    (
                        profile["bfs_number"],
                        profile["name"],
                        profile.get("kanton", "ZH"),
                        profile.get("population"),
                        profile.get("density_per_km2"),
                        profile.get("area_km2"),
                        profile.get("pv_score_pct"),
                        profile.get("pv_estimated_potential_kw"),
                        profile.get("pv_installed_kw"),
                        profile.get("pv_untapped_kw"),
                        profile.get("pv_annual_potential_gwh"),
                        profile.get("pv_snapshot_year"),
                        profile.get("pv_plant_match_rate"),
                    ),
                )
                return True
    except Exception as e:
        logger.error(f"[DB] Error upserting municipality PV: {e}")
        return False


def save_municipality_pv_panel(rows: List[Dict]) -> int:
    """Bulk-Upsert von Panel-Zeilen (bfs_number, year). Gibt Zeilenzahl zurück."""
    if not rows:
        return 0
    try:
        from psycopg2.extras import execute_values

        values = [
            (
                r["bfs_number"],
                r["year"],
                r.get("added_kw"),
                r.get("added_plants"),
                r.get("cumulative_kw"),
                r.get("estimated_potential_kw"),
                r.get("score_pct"),
                r.get("untapped_kw"),
            )
            for r in rows
        ]
        with _get_connection() as conn:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    """
                    INSERT INTO municipality_pv_panel (
                        bfs_number, year, added_kw, added_plants, cumulative_kw,
                        estimated_potential_kw, score_pct, untapped_kw)
                    VALUES %s
                    ON CONFLICT (bfs_number, year) DO UPDATE SET
                        added_kw = EXCLUDED.added_kw,
                        added_plants = EXCLUDED.added_plants,
                        cumulative_kw = EXCLUDED.cumulative_kw,
                        estimated_potential_kw = EXCLUDED.estimated_potential_kw,
                        score_pct = EXCLUDED.score_pct,
                        untapped_kw = EXCLUDED.untapped_kw
                """,
                    values,
                )
                return len(values)
    except Exception as e:
        logger.error(f"[DB] Error saving PV panel rows: {e}")
        return 0


def get_pv_profiles(kanton: Optional[str] = None) -> List[Dict]:
    """Alle Gemeinden mit berechnetem PV-Score, für die Rangliste."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                if kanton:
                    cur.execute(
                        """
                        SELECT bfs_number, name, kanton, population, density_per_km2,
                               area_km2, pv_score_pct, pv_estimated_potential_kw,
                               pv_installed_kw, pv_untapped_kw, pv_annual_potential_gwh,
                               pv_snapshot_year, pv_plant_match_rate
                        FROM municipality_profiles
                        WHERE pv_score_pct IS NOT NULL AND kanton = %s
                        ORDER BY pv_score_pct DESC
                        """,
                        (kanton,),
                    )
                else:
                    cur.execute(
                        """
                        SELECT bfs_number, name, kanton, population, density_per_km2,
                               area_km2, pv_score_pct, pv_estimated_potential_kw,
                               pv_installed_kw, pv_untapped_kw, pv_annual_potential_gwh,
                               pv_snapshot_year, pv_plant_match_rate
                        FROM municipality_profiles
                        WHERE pv_score_pct IS NOT NULL
                        ORDER BY pv_score_pct DESC
                        """
                    )
                return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error getting PV profiles: {e}")
        return []


def get_pv_movers() -> List[Dict]:
    """Fortschritt je Gemeinde: Delta des Panel-Scores im letzten vollen Jahr.

    Nur aus dem Panel berechnet, nie mit dem Snapshot vermischt.
    """
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT cur.bfs_number, mp.name, mp.kanton, mp.population,
                           mp.density_per_km2, cur.year AS year,
                           cur.score_pct AS score_now,
                           prev.score_pct AS score_prev,
                           (cur.score_pct - prev.score_pct) AS delta
                    FROM municipality_pv_panel cur
                    JOIN municipality_pv_panel prev
                      ON prev.bfs_number = cur.bfs_number
                     AND prev.year = cur.year - 1
                    JOIN municipality_profiles mp
                      ON mp.bfs_number = cur.bfs_number
                    WHERE cur.year = (SELECT MAX(year) FROM municipality_pv_panel)
                    ORDER BY delta DESC
                    """
                )
                return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error getting PV movers: {e}")
        return []


def get_municipality_pv_panel(bfs_number: int) -> List[Dict]:
    """Panel-Zeilen einer Gemeinde, nach Jahr aufsteigend."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM municipality_pv_panel WHERE bfs_number = %s ORDER BY year",
                    (bfs_number,),
                )
                return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error getting PV panel: {e}")
        return []
