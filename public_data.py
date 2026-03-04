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
    """Batch fetch ElCom tariffs for multiple municipalities (legacy, uses bulk)."""
    if bfs_numbers is None:
        bfs_numbers = ZH_BFS_NUMBERS
    return fetch_elcom_tariffs_bulk(bfs_numbers, year)


ELCOM_SPARQL_BULK_TEMPLATE = """
PREFIX schema: <http://schema.org/>
PREFIX cube: <https://cube.link/>
PREFIX elcom: <https://energy.ld.admin.ch/elcom/electricityprice/dimension/>

SELECT ?bfs ?operator ?category ?total ?energy ?grid ?municipality_fee ?kev
WHERE {{
  VALUES ?municipality {{ {values_block} }}

  ?obs a cube:Observation ;
       elcom:municipality ?municipality ;
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

  BIND(REPLACE(STR(?municipality), "https://ld.admin.ch/municipality/", "") AS ?bfs)
}}
ORDER BY ?operator ?category
"""


def fetch_elcom_tariffs_bulk(bfs_numbers: List[int], year: int = 2026, chunk_size: int = 50) -> List[Dict]:
    """Bulk SPARQL query with VALUES block, chunked to avoid timeout. Returns same shape as fetch_elcom_tariffs()."""
    if not bfs_numbers:
        return []

    all_tariffs = []
    chunks = [bfs_numbers[i:i + chunk_size] for i in range(0, len(bfs_numbers), chunk_size)]

    for chunk in chunks:
        values_block = " ".join(f"<https://ld.admin.ch/municipality/{bfs}>" for bfs in chunk)
        sparql = ELCOM_SPARQL_BULK_TEMPLATE.format(values_block=values_block, year=year)
        try:
            resp = requests.post(
                LINDAS_ENDPOINT,
                data={"query": sparql},
                headers={"Accept": "application/sparql-results+json"},
                timeout=60
            )
            resp.raise_for_status()
            data = resp.json()
            for binding in data.get("results", {}).get("bindings", []):
                bfs_val = binding.get("bfs", {}).get("value", "")
                try:
                    bfs_int = int(bfs_val)
                except (ValueError, TypeError):
                    bfs_int = 0
                all_tariffs.append({
                    "bfs_number": bfs_int,
                    "year": year,
                    "operator_name": binding.get("operator", {}).get("value", ""),
                    "category": binding.get("category", {}).get("value", ""),
                    "total_rp_kwh": _parse_decimal(binding.get("total")),
                    "energy_rp_kwh": _parse_decimal(binding.get("energy")),
                    "grid_rp_kwh": _parse_decimal(binding.get("grid")),
                    "municipality_fee_rp_kwh": _parse_decimal(binding.get("municipality_fee")),
                    "kev_rp_kwh": _parse_decimal(binding.get("kev")),
                })
        except Exception as e:
            logger.error(f"[PUBLIC_DATA] Bulk ElCom fetch failed for chunk of {len(chunk)}: {e}")

    logger.info(f"[PUBLIC_DATA] Bulk ElCom: {len(all_tariffs)} total records for {len(bfs_numbers)} municipalities")
    return all_tariffs


ELCOM_DISCOVER_MUNICIPALITIES_SPARQL = """
PREFIX schema: <http://schema.org/>
PREFIX cube: <https://cube.link/>
PREFIX elcom: <https://energy.ld.admin.ch/elcom/electricityprice/dimension/>

SELECT DISTINCT ?bfs ?name
WHERE {{
  ?obs a cube:Observation ;
       elcom:municipality ?municipality ;
       elcom:period "{year}"^^<http://www.w3.org/2001/XMLSchema#gYear> .

  ?municipality schema:name ?name .
  BIND(REPLACE(STR(?municipality), "https://ld.admin.ch/municipality/", "") AS ?bfs)
}}
ORDER BY ?bfs
"""


def fetch_elcom_municipalities(year: int = 2026) -> List[Dict]:
    """Discover all municipalities that have ElCom tariff data via SPARQL.
    Returns list of {'bfs_number': int, 'name': str}."""
    sparql = ELCOM_DISCOVER_MUNICIPALITIES_SPARQL.format(year=year)
    try:
        resp = requests.post(
            LINDAS_ENDPOINT,
            data={"query": sparql},
            headers={"Accept": "application/sparql-results+json"},
            timeout=60
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for binding in data.get("results", {}).get("bindings", []):
            bfs_val = binding.get("bfs", {}).get("value", "")
            name = binding.get("name", {}).get("value", "")
            try:
                bfs_int = int(bfs_val)
            except (ValueError, TypeError):
                continue
            results.append({"bfs_number": bfs_int, "name": name})
        logger.info(f"[PUBLIC_DATA] ElCom municipality discovery: {len(results)} municipalities for {year}")
        return results
    except Exception as e:
        logger.error(f"[PUBLIC_DATA] ElCom municipality discovery failed: {e}")
        return []


# Canton -> BFS range mapping (approximate, used for filtering SPARQL results)
KANTON_BFS_RANGES = {
    'ZH': (1, 299), 'BE': (301, 999), 'LU': (1001, 1150), 'UR': (1201, 1220),
    'SZ': (1301, 1375), 'OW': (1401, 1407), 'NW': (1501, 1511), 'GL': (1601, 1632),
    'ZG': (1701, 1711), 'FR': (2001, 2340), 'SO': (2401, 2620), 'BS': (2701, 2703),
    'BL': (2761, 2900), 'SH': (2901, 2975), 'AR': (3001, 3025), 'AI': (3101, 3111),
    'SG': (3201, 3440), 'GR': (3501, 3990), 'AG': (4001, 4295), 'TG': (4401, 4950),
    'TI': (5001, 5400), 'VD': (5401, 5940), 'VS': (6001, 6300), 'NE': (6401, 6512),
    'GE': (6601, 6645), 'JU': (6701, 6810),
}


def _filter_bfs_by_kanton(bfs_list: List[int], kanton: str) -> List[int]:
    """Filter BFS numbers by canton range."""
    kanton = kanton.upper()
    if kanton not in KANTON_BFS_RANGES:
        return bfs_list
    lo, hi = KANTON_BFS_RANGES[kanton]
    return [b for b in bfs_list if lo <= b <= hi]


def _bfs_to_kanton(bfs: int) -> str:
    """Infer canton code from BFS number range."""
    for k, (lo, hi) in KANTON_BFS_RANGES.items():
        if lo <= bfs <= hi:
            return k
    return ""


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
    all_bfs = set(er_by_bfs.keys())
    if kanton.upper() == 'ZH':
        all_bfs |= set(ZH_BFS_NUMBERS)

    # Fallback: discover BFS numbers from ElCom SPARQL when ER/Sonnendach empty
    if not all_bfs:
        elcom_munis = fetch_elcom_municipalities(year)
        elcom_bfs_map = {m['bfs_number']: m['name'] for m in elcom_munis}
        canton_bfs = _filter_bfs_by_kanton(list(elcom_bfs_map.keys()), kanton)
        all_bfs = set(canton_bfs)
        result["bfs_source"] = "elcom_sparql"
        # Pre-populate name map for municipalities discovered via SPARQL
        for bfs in canton_bfs:
            if bfs not in er_by_bfs:
                er_by_bfs[bfs] = {"name": elcom_bfs_map.get(bfs, ""), "kanton": kanton}

    # Bulk fetch ElCom tariffs for all BFS at once
    bulk_tariffs = fetch_elcom_tariffs_bulk(list(all_bfs), year)
    if bulk_tariffs:
        db.save_elcom_tariffs(bulk_tariffs)
    # Index by BFS
    tariffs_by_bfs = {}
    for t in bulk_tariffs:
        tariffs_by_bfs.setdefault(t['bfs_number'], []).append(t)

    for bfs in all_bfs:
        try:
            er = er_by_bfs.get(bfs, {})
            sd = sd_by_bfs.get(bfs, {})

            # ElCom tariffs (from bulk result)
            tariffs = tariffs_by_bfs.get(bfs, [])

            h4 = next((t for t in tariffs if t.get("category", "").startswith("H4")), None)
            value_gap = compute_leg_value_gap(h4) if h4 else {"annual_savings_chf": 0}

            # Name: Energie Reporter > municipalities table > ElCom operator > empty
            name = er.get("name", "")
            if not name:
                muni = db.get_municipality(bfs_number=bfs)
                if muni:
                    name = muni.get("name", "")
            if not name:
                # Use operator name from tariffs as last resort
                for t in tariffs:
                    op = t.get("operator_name", "")
                    if op:
                        name = op.split(" ")[0]  # first word of operator
                        break
            profile = {
                "bfs_number": bfs,
                "name": name,
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


def refresh_all_municipalities(year: int = 2026) -> Dict:
    """Bulk refresh all Swiss municipalities from Energie Reporter + Sonnendach.

    Skips per-BFS ElCom SPARQL (too slow for 2131 municipalities).
    ElCom data is added incrementally via refresh_canton() per canton.
    """
    import database as db

    result = {"scope": "all", "municipalities": 0, "errors": [], "elcom_calls": 0}

    # 1. Energie Reporter (all cantons)
    er_data = fetch_energie_reporter()
    er_by_bfs = {}
    for entry in er_data:
        bfs = entry.get("bfs_number")
        if bfs:
            er_by_bfs[bfs] = entry

    # 2. Sonnendach (all municipalities)
    sd_data = fetch_sonnendach_municipal()
    sd_by_bfs = {}
    for entry in sd_data:
        bfs = entry.get("bfs_number")
        if bfs:
            sd_by_bfs[bfs] = entry
            db.save_sonnendach_municipal(entry)

    # 3. Merge all BFS numbers and save profiles (no ElCom)
    all_bfs = set(er_by_bfs.keys()) | set(sd_by_bfs.keys())

    # Fallback: discover all municipalities from ElCom SPARQL
    if not all_bfs:
        elcom_munis = fetch_elcom_municipalities(year)
        for m in elcom_munis:
            bfs = m['bfs_number']
            all_bfs.add(bfs)
            if bfs not in er_by_bfs:
                er_by_bfs[bfs] = {"name": m['name'], "kanton": _bfs_to_kanton(bfs)}
        result["bfs_source"] = "elcom_sparql"

    for bfs in all_bfs:
        try:
            er = er_by_bfs.get(bfs, {})
            sd = sd_by_bfs.get(bfs, {})

            name = er.get("name", "")
            if not name:
                muni = db.get_municipality(bfs_number=bfs)
                if muni:
                    name = muni.get("name", "")
            kanton_code = er.get("kanton", "") or _bfs_to_kanton(bfs)
            profile = {
                "bfs_number": bfs,
                "name": name,
                "kanton": kanton_code,
                "population": er.get("population"),
                "solar_potential_pct": er.get("solar_potential_pct"),
                "solar_installed_kwp": sd.get("potential_kwp"),
                "ev_share_pct": er.get("ev_share_pct"),
                "renewable_heating_pct": er.get("renewable_heating_pct"),
                "electricity_consumption_mwh": er.get("electricity_consumption_mwh"),
                "renewable_production_mwh": er.get("renewable_production_mwh"),
                "leg_value_gap_chf": 0,  # No ElCom in bulk mode
                "data_sources": {
                    "elcom": False,
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


# === VNB Transparency Scoring ===

def compute_vnb_transparency_score(tariffs: List[Dict], municipalities_served: int = 0) -> float:
    """Score a DSO's tariff transparency 0-100.

    Factors (weighted):
    - Category coverage: how many H-categories have data (30%)
    - Component completeness: energy, grid, fee, kev filled (30%)
    - Municipality coverage: more served = more transparent (20%)
    - Data freshness: having any tariff at all (20%)
    """
    if not tariffs:
        return 0.0

    # Category coverage (H1-H8 typical)
    categories = {t.get('category', '') for t in tariffs if t.get('category')}
    cat_score = min(len(categories) / 4.0, 1.0)  # 4+ categories = full score

    # Component completeness across all tariffs
    components = ['energy_rp_kwh', 'grid_rp_kwh', 'municipality_fee_rp_kwh', 'kev_rp_kwh']
    filled = 0
    total = 0
    for t in tariffs:
        for c in components:
            total += 1
            val = t.get(c)
            if val is not None and val != 0:
                filled += 1
    comp_score = filled / total if total > 0 else 0.0

    # Municipality coverage
    muni_score = min(municipalities_served / 20.0, 1.0)  # 20+ = full score

    # Data presence
    presence_score = 1.0

    score = (cat_score * 30 + comp_score * 30 + muni_score * 20 + presence_score * 20)
    return round(min(max(score, 0), 100), 1)


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
