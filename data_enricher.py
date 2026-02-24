import requests
import pandas as pd
import numpy as np
import time
import hashlib
import re

# Importiere Profil-Generator aus ml_models
import ml_models 

# --- API-Endpunkte ---
GEO_API_URL = "https://api3.geo.admin.ch/rest/services/api/SearchServer"
SOLAR_API_URL = "https://api3.geo.admin.ch/rest/services/api/MapServer/ch.bfe.sonnendach"

# --- Mock-Funktionen (Simulation von GWR/MOFIS-Datenbanken) ---
def mock_get_gwr_data(lat, lon, plz):
    """Simuliert eine GWR-Abfrage basierend auf PLZ"""
    if plz in [5430, 5432]: return "EFH", 1, 160 # Wettingen/Neuenhof
    elif plz == 5400: return "MFH", 8, 700 # Baden Zentrum
    else: return "EFH", 1, 150 # Default

def mock_get_plz_stats(plz):
    """Simuliert eine Statistik-Abfrage (EV-Dichte, Kaufkraft)"""
    if plz == 5400: return 15.0, 1.2 # Baden
    elif plz == 5430: return 10.0, 1.0 # Wettingen
    else: return 5.0, 0.9 # Umland

# --- Echte API-Funktionen (Opendata) ---
def _plz_in_ranges(plz_int, plz_ranges=None):
    """Check if a PLZ falls within any of the given ranges. Default: Zürich (8000-8999)."""
    if plz_ranges is None:
        plz_ranges = [[8000, 8999]]
    return any(lo <= plz_int <= hi for lo, hi in plz_ranges)


def get_address_suggestions(query_string, limit=10, plz_ranges=None):
    """Fragt Swisstopo API ab, um Adressvorschläge zu erhalten (gefiltert nach PLZ-Bereichen)."""
    if not query_string or len(query_string) < 2:
        return []

    # Erhöhe Limit für API, da wir nachher filtern
    params = {'searchText': query_string, 'type': 'locations', 'limit': limit * 3}
    try:
        response = requests.get(GEO_API_URL, params=params, timeout=5)
        response.raise_for_status()
        results = response.json().get('results', [])

        suggestions = []
        for result in results:
            attrs = result.get('attrs', {})
            label = attrs.get('label', '')

            # Filter by PLZ ranges
            plz = attrs.get('plz')
            if plz:
                try:
                    plz_int = int(plz)
                    if not _plz_in_ranges(plz_int, plz_ranges):
                        continue
                except (ValueError, TypeError):
                    plz_match = re.search(r'\b(\d{4})\b', label)
                    if not plz_match:
                        continue
                    plz_int = int(plz_match.group(1))
                    if not _plz_in_ranges(plz_int, plz_ranges):
                        continue
            else:
                plz_match = re.search(r'\b(\d{4})\b', label)
                if not plz_match:
                    continue
                plz_int = int(plz_match.group(1))
                if not _plz_in_ranges(plz_int, plz_ranges):
                    continue

            # Entferne HTML-Tags aus dem Label
            if label:
                clean_label = re.sub(r'<[^>]+>', '', label)
                suggestions.append({
                    'label': clean_label,
                    'lat': attrs.get('lat'),
                    'lon': attrs.get('lon'),
                    'plz': plz if plz else plz_int
                })

                if len(suggestions) >= limit:
                    break

        return suggestions
    except Exception as e:
        print(f"  [GEO FEHLER bei Vorschlägen] {e}")
        return []

def get_coordinates_from_address(address_string):
    """Fragt Swisstopo API ab, um Adresse in LV95-Koordinaten umzuwandeln."""
    print(f"[GEO] Suche Koordinaten für: '{address_string}'")
    params = {'searchText': address_string, 'type': 'locations', 'limit': 1}
    try:
        response = requests.get(GEO_API_URL, params=params)
        response.raise_for_status()
        results = response.json().get('results', [])
        if not results: 
            print("  [GEO FEHLER] Adresse nicht gefunden.")
            return None, None, None
        attrs = results[0]['attrs']
        # LV95 Koordinaten (Y = lat, X = lon)
        plz = attrs.get('plz')
        if not plz:
            # Versuche PLZ aus dem Label zu extrahieren
            label = attrs.get('label', '')
            plz_match = re.search(r'\b(\d{4})\b', label)
            plz = int(plz_match.group(1)) if plz_match else None
        return attrs['lat'], attrs['lon'], plz
    except Exception as e:
        print(f"  [GEO FEHLER] {e}")
        return None, None, None

def get_pv_potential_from_coords(lat, lon):
    """Fragt BFE Sonnendach API ab, um PV-Potenzial (basierend auf Luftbildern) zu erhalten."""
    print(f"[GEO] Suche PV-Potenzial bei ({lat}, {lon})...")
    geometry = f"{lon},{lat}" # API erwartet X,Y (lon,lat)
    params = {
        'geometry': geometry, 'geometryType': 'esriGeometryPoint',
        'mapExtent': f"{lon-10},{lat-10},{lon+10},{lat+10}",
        'imageDisplay': '1,1,1', 'tolerance': 2, 'returnGeometry': 'false',
        'layers': 'all:ch.bfe.sonnendach',
    }
    try:
        response = requests.get(f"{SOLAR_API_URL}/identify", params=params)
        response.raise_for_status()
        results = response.json().get('results', [])
        if not results: 
            print("  [GEO FEHLER] Kein Gebäude für PV-Potenzial gefunden.")
            return 0, 0
        attrs = results[0]['attributes']
        # 'strom_a' = Jährliche Stromproduktion von *bestens* geeigneter Fläche (kWh)
        potential_kwh_pa = attrs.get('strom_a', 0)
        # Heuristik: 1 kWp produziert ca. 1000 kWh/a
        potential_kwp = potential_kwh_pa / 1000.0 
        print(f"  [GEO OK] -> Produktion: {potential_kwh_pa} kWh/a ({potential_kwp:.1f} kWp)")
        return potential_kwh_pa, potential_kwp
    except Exception as e:
        print(f"  [GEO FEHLER] {e}")
        return 0, 0

# --- Schätz-Funktionen (Logik) ---
def estimate_consumption_kwh(building_type, num_apartments, energy_surface_m2, plz_stats):
    """Schätzt Grundverbrauch (ohne EV/WP) basierend auf GWR-Typ."""
    if building_type == "EFH": consumption = 4500
    elif building_type == "MFH": consumption = num_apartments * 2500
    else: consumption = energy_surface_m2 * 20 # Gewerbe-Heuristik
    consumption *= plz_stats[1] # Kaufkraft-Index
    return consumption

def estimate_ev_kwh(building_type, plz_stats):
    """Schätzt wahrscheinlichen EV-Ladebedarf."""
    ev_penetration, income_index = plz_stats
    probability = 0.0
    if building_type == "EFH": # EFH hat höhere Wahrscheinlichkeit für private Ladestation
        probability = (ev_penetration / 100.0) * income_index
    
    if np.random.rand() < probability:
        print(f"  [ESTIMATE] EV-Bedarf: Ja (Chance: {probability*100:.1f}%)")
        return 2500.0 # EV-Jahresbedarf
    print(f"  [ESTIMATE] EV-Bedarf: Nein (Chance: {probability*100:.1f}%)")
    return 0.0

# --- Haupt-Wrapper-Funktionen ---

def get_energy_profile_for_address(address_string):
    """
    Haupt-Workflow (ECHTE API-AUFRUFE): Nimmt eine Adresse und gibt ein Profil-Dict zurück.
    """
    # Bereinige Adresse: entferne HTML-Tags und extra Whitespace
    clean_address = re.sub(r'<[^>]+>', '', address_string).strip()
    print(f"--- [ENRICHER] Starte ECHTE Analyse für: {clean_address} ---")
    
    # 0. Eindeutige ID generieren (aus Adresse gehasht)
    building_id = hashlib.md5(clean_address.encode()).hexdigest()[:10]
    
    # 1. Adresse -> Koordinaten (Echte API)
    lat, lon, plz = get_coordinates_from_address(clean_address)
    if not lat: 
        print(f"  [ENRICHER] Keine Koordinaten gefunden für: {clean_address}")
        return None, None
        
    # 2. Koordinaten -> PV-Potenzial (Echte API)
    pv_kwh_pa, pv_kwp = get_pv_potential_from_coords(lat, lon)
    
    # 3. Statistische Daten (Simulierte DB)
    gwr_data = mock_get_gwr_data(lat, lon, plz)
    plz_stats = mock_get_plz_stats(plz)
    
    # 4. Jahreswerte schätzen
    base_consumption_kwh_pa = estimate_consumption_kwh(gwr_data[0], gwr_data[1], gwr_data[2], plz_stats)
    ev_kwh_pa = estimate_ev_kwh(gwr_data[0], plz_stats)
    
    final_estimates = {
        "building_id": building_id,
        "address": clean_address,  # Verwende bereinigte Adresse
        "plz": plz,
        "lat": lat,
        "lon": lon,
        "building_type": gwr_data[0],
        "annual_consumption_kwh": base_consumption_kwh_pa + ev_kwh_pa,
        "potential_pv_kwp": pv_kwp
    }
    
    # 5. Profile generieren (aus ml_models.py importiert)
    final_profiles = ml_models.generate_mock_profiles(
        annual_consumption_kwh=base_consumption_kwh_pa + ev_kwh_pa,
        potential_pv_kwp=pv_kwp
    )
    
    print("--- [ENRICHER] Analyse abgeschlossen ---")
    return final_estimates, final_profiles

def get_mock_energy_profile_for_address(address_string):
    """
    (Schnell) Entwicklungs-Funktion: Überspringt externe APIs und gibt
    schnell plausible, zufällige Mock-Daten zurück.
    """
    print(f"--- [MOCK ENRICHER] Starte MOCK-Analyse für: {address_string} ---")
    
    # 0. Eindeutige ID
    building_id = hashlib.md5(address_string.encode()).hexdigest()[:10]
    
    # 1. Mock-Koordinaten & PLZ
    np.random.seed(len(address_string)) # Seed, damit Adresse immer gleiche Mocks gibt
    base_lat, base_lon = 47.473, 8.308
    lat = base_lat + np.random.randn() * 0.005
    lon = base_lon + np.random.randn() * 0.005
    plz = int(np.random.choice([5400, 5430, 5432])) # Als int
    
    # 2. Mock-Schätzungen
    building_type = np.random.choice(['EFH', 'MFH'], p=[0.7, 0.3])
    annual_consumption_kwh = float(np.random.randint(4000, 20000))
    potential_pv_kwp = float(np.random.randint(0, 30))
    
    final_estimates = {
        "building_id": building_id,
        "address": address_string,
        "plz": plz,
        "lat": lat,
        "lon": lon,
        "building_type": building_type,
        "annual_consumption_kwh": annual_consumption_kwh,
        "potential_pv_kwp": potential_pv_kwp
    }

    # 3. Profile generieren
    final_profiles = ml_models.generate_mock_profiles(
        annual_consumption_kwh=annual_consumption_kwh,
        potential_pv_kwp=potential_pv_kwp
    )

    print("--- [MOCK ENRICHER] Analyse abgeschlossen ---")
    return final_estimates, final_profiles


if __name__ == "__main__":
    print("Dieses Skript ist ein Modul und sollte von app.py importiert werden.")
    print("Testaufruf der ECHTEN API mit 'Stadtturm, 5400 Baden':")
    estimates, _ = get_energy_profile_for_address("Stadtturm, 5400 Baden")
    print("\nErgebnis des Testaufrufs:")
    print(estimates)

