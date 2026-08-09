# SPDX-License-Identifier: AGPL-3.0-or-later
"""Gemeinde-Energieprofil-Speicher (ElCom-Tarife, Gemeindeprofile, Sonnendach).

Repository module for the municipality energy profile data domain: ElCom
tariffs, municipality profiles, and Sonnendach municipal solar data. The
connection seam is resolved via ``database.get_connection`` at call time so
existing tests that ``monkeypatch.setattr(database, "get_connection", ...)``
keep working unchanged and ``database`` can re-export these functions for
legacy callers.
"""

import logging

logger = logging.getLogger(__name__)


def _get_connection():
    import database

    return database.get_connection()


# === ElCom Tariff Operations ===


def save_elcom_tariffs(tariffs: list[dict]) -> int:
    """Bulk upsert ElCom tariff records. Returns count saved."""
    if not tariffs:
        return 0
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                count = 0
                for t in tariffs:
                    cur.execute(
                        """
                        INSERT INTO elcom_tariffs (bfs_number, operator_name, year, category,
                            total_rp_kwh, energy_rp_kwh, grid_rp_kwh, municipality_fee_rp_kwh, kev_rp_kwh)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (bfs_number, operator_name, year, category) DO UPDATE SET
                            total_rp_kwh = EXCLUDED.total_rp_kwh,
                            energy_rp_kwh = EXCLUDED.energy_rp_kwh,
                            grid_rp_kwh = EXCLUDED.grid_rp_kwh,
                            municipality_fee_rp_kwh = EXCLUDED.municipality_fee_rp_kwh,
                            kev_rp_kwh = EXCLUDED.kev_rp_kwh,
                            fetched_at = CURRENT_TIMESTAMP
                    """,
                        (
                            t["bfs_number"],
                            t.get("operator_name", ""),
                            t["year"],
                            t["category"],
                            t.get("total_rp_kwh"),
                            t.get("energy_rp_kwh"),
                            t.get("grid_rp_kwh"),
                            t.get("municipality_fee_rp_kwh"),
                            t.get("kev_rp_kwh"),
                        ),
                    )
                    count += 1
                return count
    except Exception as e:
        logger.error(f"[DB] Error saving ElCom tariffs: {e}")
        return 0


def get_elcom_tariffs(bfs_number: int, year: int | None = None) -> list[dict]:
    """Get ElCom tariffs for a municipality."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            if year:
                cur.execute(
                    """
                        SELECT * FROM elcom_tariffs
                        WHERE bfs_number = %s AND year = %s
                        ORDER BY category
                    """,
                    (bfs_number, year),
                )
            else:
                cur.execute(
                    """
                        SELECT * FROM elcom_tariffs
                        WHERE bfs_number = %s
                        ORDER BY year DESC, category
                    """,
                    (bfs_number,),
                )
            return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error getting ElCom tariffs: {e}")
        return []


# === Municipality Profile Operations ===


def save_municipality_profile(profile: dict) -> bool:
    """Upsert a municipality profile."""
    try:
        import json

        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO municipality_profiles (bfs_number, name, kanton, population,
                        solar_potential_pct, solar_installed_kwp, ev_share_pct, renewable_heating_pct,
                        electricity_consumption_mwh, renewable_production_mwh,
                        leg_value_gap_chf, energy_transition_score, data_sources)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (bfs_number) DO UPDATE SET
                        name = EXCLUDED.name, kanton = EXCLUDED.kanton, population = EXCLUDED.population,
                        solar_potential_pct = EXCLUDED.solar_potential_pct,
                        solar_installed_kwp = EXCLUDED.solar_installed_kwp,
                        ev_share_pct = EXCLUDED.ev_share_pct,
                        renewable_heating_pct = EXCLUDED.renewable_heating_pct,
                        electricity_consumption_mwh = EXCLUDED.electricity_consumption_mwh,
                        renewable_production_mwh = EXCLUDED.renewable_production_mwh,
                        leg_value_gap_chf = EXCLUDED.leg_value_gap_chf,
                        energy_transition_score = EXCLUDED.energy_transition_score,
                        data_sources = EXCLUDED.data_sources,
                        updated_at = CURRENT_TIMESTAMP
                """,
                    (
                        profile["bfs_number"],
                        profile["name"],
                        profile.get("kanton", "ZH"),
                        profile.get("population"),
                        profile.get("solar_potential_pct"),
                        profile.get("solar_installed_kwp"),
                        profile.get("ev_share_pct"),
                        profile.get("renewable_heating_pct"),
                        profile.get("electricity_consumption_mwh"),
                        profile.get("renewable_production_mwh"),
                        profile.get("leg_value_gap_chf"),
                        profile.get("energy_transition_score"),
                        json.dumps(profile.get("data_sources", {})),
                    ),
                )
                return True
    except Exception as e:
        logger.error(f"[DB] Error saving municipality profile: {e}")
        return False


def get_municipality_profile(bfs_number: int) -> dict | None:
    """Get a municipality profile by BFS number."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM municipality_profiles WHERE bfs_number = %s",
                (bfs_number,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"[DB] Error getting municipality profile: {e}")
        return None


def get_all_municipality_profiles(
    kanton: str | None = None, order_by: str = "name"
) -> list[dict]:
    """Get all municipality profiles, optionally filtered by kanton."""
    # Map the requested sort key to a fixed column literal. The value
    # interpolated into ORDER BY is always one of these constants (never the
    # caller's string), so the clause cannot carry untrusted input.
    order_columns = {
        "name": "name",
        "population": "population",
        "energy_transition_score": "energy_transition_score",
        "leg_value_gap_chf": "leg_value_gap_chf",
        "pv_score_pct": "pv_score_pct",
        "bfs_number": "bfs_number",
    }
    order_column = order_columns.get(order_by, "name")
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            if kanton:
                cur.execute(
                    f"""
                        SELECT * FROM municipality_profiles
                        WHERE kanton = %s ORDER BY {order_column}
                    """,
                    (kanton,),
                )
            else:
                cur.execute(
                    f"SELECT * FROM municipality_profiles ORDER BY {order_column}"
                )
            return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error getting municipality profiles: {e}")
        return []


def search_municipality_profiles(q: str, limit: int = 10) -> list[dict]:
    """Search municipality profiles by name (case-insensitive substring)."""
    q = (q or "").strip()
    if not q:
        return []
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT * FROM municipality_profiles
                    WHERE name ILIKE %s
                    ORDER BY name
                    LIMIT %s
                """,
                (f"%{q}%", max(1, min(int(limit), 50))),
            )
            return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error searching municipality profiles: {e}")
        return []


def get_all_municipality_profile_bfs_numbers() -> list[int]:
    """Get sorted BFS numbers from municipality_profiles."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT bfs_number FROM municipality_profiles ORDER BY bfs_number"
            )
            return [int(row["bfs_number"]) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error getting municipality profile BFS list: {e}")
        return []


def get_profile_bfs_missing_elcom_tariffs(year: int, limit: int = 50) -> list[int]:
    """Get BFS numbers with a profile but without elcom_tariffs rows for the target year."""
    safe_limit = max(1, min(int(limit), 500))
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT p.bfs_number
                    FROM municipality_profiles p
                    LEFT JOIN elcom_tariffs t
                      ON t.bfs_number = p.bfs_number AND t.year = %s
                    WHERE t.bfs_number IS NULL
                    ORDER BY p.bfs_number
                    LIMIT %s
                    """,
                (int(year), safe_limit),
            )
            return [int(row["bfs_number"]) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error getting missing ElCom BFS list: {e}")
        return []


# === Sonnendach Municipal Operations ===


def save_sonnendach_municipal(data: dict) -> bool:
    """Upsert sonnendach municipal solar data."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO sonnendach_municipal (bfs_number, total_roof_area_m2, suitable_roof_area_m2,
                        potential_kwh_year, potential_kwp, utilization_pct)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (bfs_number) DO UPDATE SET
                        total_roof_area_m2 = EXCLUDED.total_roof_area_m2,
                        suitable_roof_area_m2 = EXCLUDED.suitable_roof_area_m2,
                        potential_kwh_year = EXCLUDED.potential_kwh_year,
                        potential_kwp = EXCLUDED.potential_kwp,
                        utilization_pct = EXCLUDED.utilization_pct,
                        fetched_at = CURRENT_TIMESTAMP
                """,
                    (
                        data["bfs_number"],
                        data.get("total_roof_area_m2"),
                        data.get("suitable_roof_area_m2"),
                        data.get("potential_kwh_year"),
                        data.get("potential_kwp"),
                        data.get("utilization_pct"),
                    ),
                )
                return True
    except Exception as e:
        logger.error(f"[DB] Error saving sonnendach data: {e}")
        return False


def get_sonnendach_municipal(bfs_number: int) -> dict | None:
    """Get sonnendach data for a municipality."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM sonnendach_municipal WHERE bfs_number = %s",
                (bfs_number,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"[DB] Error getting sonnendach data: {e}")
        return None
