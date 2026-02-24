"""
Public data fetchers for OpenLEG.
Aggregates Swiss government open data: ElCom tariffs, Energie Reporter, Sonnendach.
All functions are pure: fetch, parse, return dict. DB persistence handled by callers.
"""
import logging
import csv
import io
from typing import Dict, List, Optional
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

# === SPARQL / ElCom ===

LINDAS_ENDPOINT = "https://lindas.admin.ch/query"

ELCOM_SPARQL_TEMPLATE = """
PREFIX schema: <http://schema.org/>
PREFIX cube: <https://cube.link/>
PREFIX elcom: <https://energy.ld.admin.ch/elcom/electricityprice/dimension/>

SELECT ?operator ?category ?total ?energy ?grid ?municipality_fee ?kev
WHERE {{
  ?obs a cube:Observation ;
       elcom:municipality <https://ld.admin.ch/municipality/{bfs}> ;
       elcom:period "{year}"^^<http://www.w3.org/2001/XMLSchema#gYear> ;
       elcom:operator ?operatorUri ;
       elcom:category ?categoryUri ;
       elcom:total ?total .

  OPTIONAL {{ ?obs elcom:gridusage ?grid }}
  OPTIONAL {{ ?obs elcom:energy ?energy }}
  OPTIONAL {{ ?obs elcom:charge ?municipality_fee }}
  OPTIONAL {{ ?obs elcom:aidfee ?kev }}

  ?operatorUri schema:name ?operator .
  ?categoryUri schema:name ?category .
}}
ORDER BY ?operator ?category
"""


def fetch_elcom_tariffs(bfs_number: int, year: int = 2026) -> List[Dict]:
    """Query LINDAS SPARQL endpoint for ElCom tariffs of a municipality."""
    sparql = ELCOM_SPARQL_TEMPLATE.format(bfs=bfs_number, year=year)
    try:
        resp = requests.post(
            LINDAS_ENDPOINT,
            data={"query": sparql},
            headers={"Accept": "application/sparql-results+json"},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for binding in data.get("results", {}).get("bindings", []):
            results.append({
                "bfs_number": bfs_number,
                "year": year,
                "operator_name": binding.get("operator", {}).get("value", ""),
                "category": binding.get("category", {}).get("value", ""),
                "total_rp_kwh": _parse_decimal(binding.get("total")),
                "energy_rp_kwh": _parse_decimal(binding.get("energy")),
                "grid_rp_kwh": _parse_decimal(binding.get("grid")),
                "municipality_fee_rp_kwh": _parse_decimal(binding.get("municipality_fee")),
                "kev_rp_kwh": _parse_decimal(binding.get("kev")),
            })
        logger.info(f"[PUBLIC_DATA] ElCom: {len(results)} tariff records for BFS {bfs_number}/{year}")
        return results
    except Exception as e:
        logger.error(f"[PUBLIC_DATA] ElCom fetch failed for BFS {bfs_number}: {e}")
        return []


def fetch_all_elcom_tariffs(kanton: str = 'ZH', year: int = 2026, bfs_numbers: List[int] = None) -> List[Dict]:
    """Batch fetch ElCom tariffs for multiple municipalities."""
    if bfs_numbers is None:
        bfs_numbers = ZH_BFS_NUMBERS
    all_tariffs = []
    for bfs in bfs_numbers:
        tariffs = fetch_elcom_tariffs(bfs, year)
        all_tariffs.extend(tariffs)
    logger.info(f"[PUBLIC_DATA] Batch ElCom: {len(all_tariffs)} total records for {len(bfs_numbers)} municipalities")
    return all_tariffs


def _parse_decimal(binding_value):
    """Parse SPARQL decimal binding to float."""
    if not binding_value:
        return None
    try:
        return float(binding_value.get("value", 0))
    except (ValueError, TypeError):
        return None


# === Energie Reporter ===

ENERGIE_REPORTER_URL = "https://opendata.swiss/api/3/action/package_show?id=energie-reporter"


def fetch_energie_reporter() -> List[Dict]:
    """Download Energie Reporter data from opendata.swiss and parse into per-municipality dicts."""
    try:
        # Get dataset metadata to find CSV resource
        resp = requests.get(ENERGIE_REPORTER_URL, timeout=15)
        resp.raise_for_status()
        pkg = resp.json().get("result", {})
        csv_url = None
        for resource in pkg.get("resources", []):
            if resource.get("format", "").upper() == "CSV":
                csv_url = resource.get("url")
                break

        if not csv_url:
            logger.warning("[PUBLIC_DATA] Energie Reporter: no CSV resource found")
            return []

        csv_resp = requests.get(csv_url, timeout=30)
        csv_resp.raise_for_status()
        csv_resp.encoding = csv_resp.apparent_encoding or 'utf-8'

        reader = csv.DictReader(io.StringIO(csv_resp.text), delimiter=';')
        results = []
        for row in reader:
            bfs = _safe_int(row.get('BFS_NR') or row.get('bfs_nr') or row.get('gemeinde_bfs'))
            if not bfs:
                continue
            results.append({
                "bfs_number": bfs,
                "name": row.get('GEMEINDENAME') or row.get('gemeindename') or row.get('name', ''),
                "kanton": row.get('KANTON') or row.get('kanton', ''),
                "solar_potential_pct": _safe_float(row.get('anteil_dachflaechen_solar') or row.get('solar_potential_pct')),
                "ev_share_pct": _safe_float(row.get('anteil_ev') or row.get('ev_share_pct')),
                "renewable_heating_pct": _safe_float(row.get('anteil_erneuerbar_heizen') or row.get('renewable_heating_pct')),
                "electricity_consumption_mwh": _safe_float(row.get('stromverbrauch_mwh') or row.get('electricity_consumption_mwh')),
                "renewable_production_mwh": _safe_float(row.get('erneuerbare_produktion_mwh') or row.get('renewable_production_mwh')),
            })
        logger.info(f"[PUBLIC_DATA] Energie Reporter: {len(results)} municipalities parsed")
        return results
    except Exception as e:
        logger.error(f"[PUBLIC_DATA] Energie Reporter fetch failed: {e}")
        return []


# === Sonnendach ===

SONNENDACH_URL = "https://opendata.swiss/api/3/action/package_show?id=sonnendach-ch"


def fetch_sonnendach_municipal() -> List[Dict]:
    """Download municipal-level solar potential from opendata.swiss."""
    try:
        resp = requests.get(SONNENDACH_URL, timeout=15)
        resp.raise_for_status()
        pkg = resp.json().get("result", {})
        csv_url = None
        for resource in pkg.get("resources", []):
            fmt = resource.get("format", "").upper()
            name = (resource.get("name") or "").lower()
            if fmt == "CSV" and ("gemeinde" in name or "municipal" in name or "kommun" in name):
                csv_url = resource.get("url")
                break
        # Fallback: any CSV
        if not csv_url:
            for resource in pkg.get("resources", []):
                if resource.get("format", "").upper() == "CSV":
                    csv_url = resource.get("url")
                    break

        if not csv_url:
            logger.warning("[PUBLIC_DATA] Sonnendach: no CSV resource found")
            return []

        csv_resp = requests.get(csv_url, timeout=30)
        csv_resp.raise_for_status()
        csv_resp.encoding = csv_resp.apparent_encoding or 'utf-8'

        reader = csv.DictReader(io.StringIO(csv_resp.text), delimiter=';')
        results = []
        for row in reader:
            bfs = _safe_int(row.get('BFS_NR') or row.get('bfs_nr') or row.get('gemeinde_bfs'))
            if not bfs:
                continue
            results.append({
                "bfs_number": bfs,
                "total_roof_area_m2": _safe_float(row.get('dachflaeche_total_m2') or row.get('total_roof_area_m2')),
                "suitable_roof_area_m2": _safe_float(row.get('dachflaeche_geeignet_m2') or row.get('suitable_roof_area_m2')),
                "potential_kwh_year": _safe_float(row.get('potenzial_kwh_jahr') or row.get('potential_kwh_year')),
                "potential_kwp": _safe_float(row.get('potenzial_kwp') or row.get('potential_kwp')),
                "utilization_pct": _safe_float(row.get('auslastung_pct') or row.get('utilization_pct')),
            })
        logger.info(f"[PUBLIC_DATA] Sonnendach: {len(results)} municipalities parsed")
        return results
    except Exception as e:
        logger.error(f"[PUBLIC_DATA] Sonnendach fetch failed: {e}")
        return []


# === Computed Metrics ===

def compute_leg_value_gap(h4_tariff: Dict, grid_reduction_pct: float = 40.0) -> Dict:
    """
    Calculate LEG value gap from grid fee component.
    grid_reduction_pct: typical LEG grid fee reduction (40% for NE7).
    """
    grid_rp = float(h4_tariff.get('grid_rp_kwh', 0) or 0)
    total_rp = float(h4_tariff.get('total_rp_kwh', 0) or 0)
    if grid_rp <= 0 or total_rp <= 0:
        return {"annual_savings_chf": 0, "monthly_savings_chf": 0, "savings_pct": 0}

    savings_rp_kwh = grid_rp * (grid_reduction_pct / 100.0)
    # Assume H4: 4500 kWh/year typical household
    annual_kwh = 4500
    annual_savings_chf = savings_rp_kwh * annual_kwh / 100.0  # Rp to CHF

    return {
        "grid_fee_rp_kwh": round(grid_rp, 2),
        "savings_rp_kwh": round(savings_rp_kwh, 2),
        "annual_savings_chf": round(annual_savings_chf, 2),
        "monthly_savings_chf": round(annual_savings_chf / 12, 2),
        "savings_pct": round(savings_rp_kwh / total_rp * 100, 1) if total_rp > 0 else 0,
        "grid_reduction_pct": grid_reduction_pct,
        "assumed_consumption_kwh": annual_kwh,
    }


def compute_energy_transition_score(profile: Dict) -> float:
    """
    Weighted 0-100 score for energy transition progress.
    Solar 30%, EVs 20%, Heating 25%, Production 25%.
    """
    solar = min(float(profile.get('solar_potential_pct', 0) or 0), 100) / 100.0
    ev = min(float(profile.get('ev_share_pct', 0) or 0), 30) / 30.0  # 30% = max score
    heating = min(float(profile.get('renewable_heating_pct', 0) or 0), 100) / 100.0

    consumption = float(profile.get('electricity_consumption_mwh', 0) or 0)
    production = float(profile.get('renewable_production_mwh', 0) or 0)
    prod_ratio = min(production / consumption, 1.0) if consumption > 0 else 0

    score = (solar * 30) + (ev * 20) + (heating * 25) + (prod_ratio * 25)
    return round(score, 1)


# === Orchestration ===

def refresh_municipality(bfs_number: int, year: int = 2026) -> Dict:
    """Fetch all sources for one municipality, compute derived fields."""
    import database as db

    result = {"bfs_number": bfs_number, "sources": {}}

    # ElCom tariffs
    tariffs = fetch_elcom_tariffs(bfs_number, year)
    if tariffs:
        saved = db.save_elcom_tariffs(tariffs)
        result["sources"]["elcom"] = {"records": len(tariffs), "saved": saved}

    # Find H4 tariff for value-gap calculation
    h4 = next((t for t in tariffs if t.get("category", "").startswith("H4")), None)
    value_gap = compute_leg_value_gap(h4) if h4 else {"annual_savings_chf": 0}

    # Get existing profile or create stub
    existing = db.get_municipality_profile(bfs_number)
    profile = {
        "bfs_number": bfs_number,
        "name": existing.get("name", "") if existing else "",
        "kanton": existing.get("kanton", "ZH") if existing else "ZH",
        "population": existing.get("population") if existing else None,
        "solar_potential_pct": existing.get("solar_potential_pct") if existing else None,
        "solar_installed_kwp": existing.get("solar_installed_kwp") if existing else None,
        "ev_share_pct": existing.get("ev_share_pct") if existing else None,
        "renewable_heating_pct": existing.get("renewable_heating_pct") if existing else None,
        "electricity_consumption_mwh": existing.get("electricity_consumption_mwh") if existing else None,
        "renewable_production_mwh": existing.get("renewable_production_mwh") if existing else None,
        "leg_value_gap_chf": value_gap.get("annual_savings_chf", 0),
        "data_sources": {"elcom": True, "last_refresh": datetime.now().isoformat()},
    }
    profile["energy_transition_score"] = compute_energy_transition_score(profile)
    db.save_municipality_profile(profile)
    result["profile"] = profile
    result["value_gap"] = value_gap

    return result


def refresh_canton(kanton: str = 'ZH', year: int = 2026) -> Dict:
    """Batch refresh: fetch Energie Reporter + Sonnendach, then per-municipality ElCom."""
    import database as db

    result = {"kanton": kanton, "municipalities": 0, "errors": []}

    # 1. Energie Reporter (bulk)
    er_data = fetch_energie_reporter()
    er_by_bfs = {}
    for entry in er_data:
        bfs = entry.get("bfs_number")
        k = entry.get("kanton", "")
        if bfs and (not kanton or k.upper() == kanton.upper()):
            er_by_bfs[bfs] = entry
    result["energie_reporter_records"] = len(er_by_bfs)

    # 2. Sonnendach (bulk)
    sd_data = fetch_sonnendach_municipal()
    sd_by_bfs = {}
    for entry in sd_data:
        bfs = entry.get("bfs_number")
        if bfs:
            sd_by_bfs[bfs] = entry
            db.save_sonnendach_municipal(entry)
    result["sonnendach_records"] = len(sd_by_bfs)

    # 3. Merge and save profiles
    all_bfs = set(list(er_by_bfs.keys()) + ZH_BFS_NUMBERS)
    for bfs in all_bfs:
        try:
            er = er_by_bfs.get(bfs, {})
            sd = sd_by_bfs.get(bfs, {})

            # ElCom tariffs
            tariffs = fetch_elcom_tariffs(bfs, year)
            if tariffs:
                db.save_elcom_tariffs(tariffs)

            h4 = next((t for t in tariffs if t.get("category", "").startswith("H4")), None)
            value_gap = compute_leg_value_gap(h4) if h4 else {"annual_savings_chf": 0}

            profile = {
                "bfs_number": bfs,
                "name": er.get("name", ""),
                "kanton": er.get("kanton", kanton),
                "population": er.get("population"),
                "solar_potential_pct": er.get("solar_potential_pct"),
                "solar_installed_kwp": sd.get("potential_kwp"),
                "ev_share_pct": er.get("ev_share_pct"),
                "renewable_heating_pct": er.get("renewable_heating_pct"),
                "electricity_consumption_mwh": er.get("electricity_consumption_mwh"),
                "renewable_production_mwh": er.get("renewable_production_mwh"),
                "leg_value_gap_chf": value_gap.get("annual_savings_chf", 0),
                "data_sources": {
                    "elcom": bool(tariffs),
                    "energie_reporter": bfs in er_by_bfs,
                    "sonnendach": bfs in sd_by_bfs,
                    "last_refresh": datetime.now().isoformat(),
                },
            }
            profile["energy_transition_score"] = compute_energy_transition_score(profile)
            db.save_municipality_profile(profile)
            result["municipalities"] += 1
        except Exception as e:
            logger.error(f"[PUBLIC_DATA] Error refreshing BFS {bfs}: {e}")
            result["errors"].append({"bfs": bfs, "error": str(e)})

    return result


# === Helpers ===

def _safe_int(val) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(str(val).replace(',', '.'))
    except (ValueError, TypeError):
        return None


# Key ZH municipalities (BFS numbers)
ZH_BFS_NUMBERS = [
    261,  # Dietikon
    247,  # Schlieren
    242,  # Urdorf
    230,  # Winterthur
    159,  # Wädenswil
    295,  # Horgen
    191,  # Dübendorf
    62,   # Kloten
    66,   # Opfikon
    53,   # Bülach
    198,  # Uster
    296,  # Illnau-Effretikon
    261,  # Zürich (duplicate Dietikon removed, add Zürich)
]
# Remove duplicates
ZH_BFS_NUMBERS = list(set(ZH_BFS_NUMBERS))
