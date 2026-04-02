# SPDX-License-Identifier: AGPL-3.0-or-later
import pandas as pd
import numpy as np
from sklearn.cluster import DBSCAN
from sklearn.preprocessing import StandardScaler
from math import radians, sin, cos, sqrt, atan2

# --- NEU: Eigenständige Distanz-Funktion ---
def calculate_distance(lat1, lon1, lat2, lon2):
    """Calculate the great-circle distance between two points (in meters)."""
    R = 6371e3  # Earth radius in meters
    phi1 = radians(lat1)
    phi2 = radians(lat2)
    delta_phi = radians(lat2 - lat1)
    delta_lambda = radians(lon2 - lon1)

    a = sin(delta_phi / 2) * sin(delta_phi / 2) + \
        cos(phi1) * cos(phi2) * \
        sin(delta_lambda / 2) * sin(delta_lambda / 2)
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c

# --- Profil-Generator ---

def generate_mock_profiles(annual_consumption_kwh, potential_pv_kwp, num_intervals=35040):
    """Generiert vereinfachte 15-Minuten-Zeitreihenprofile."""
    timestamps = pd.date_range(start='2025-01-01', periods=num_intervals, freq='15min')
    
    # Konvertiere zu numpy arrays, um Index-Probleme zu vermeiden
    time_of_day = np.array(timestamps.hour) + np.array(timestamps.minute) / 60.0
    consumption_shape = np.cos((time_of_day - 13.5) * (np.pi / 12))**2
    # Stelle sicher, dass consumption_shape ein numpy array ist
    consumption_shape = np.array(consumption_shape)
    consumption_shape[ (time_of_day < 6) | (time_of_day > 22) ] *= 0.5
    
    # Verhindere Division durch Null, wenn Summe 0 ist
    if consumption_shape.sum() == 0:
        normalized_consumption = np.zeros_like(consumption_shape)
    else:
        normalized_consumption = consumption_shape / consumption_shape.sum()
        
    consumption_profile_kw = normalized_consumption * (annual_consumption_kwh / 0.25)
    
    # Konvertiere day_of_year zu numpy array
    day_of_year = np.array(timestamps.dayofyear)
    seasonal_factor = 1 + 0.5 * np.cos((day_of_year - 172) * (2 * np.pi / 365))
    # Berechne pv_shape mit numpy, um Index-Probleme zu vermeiden
    pv_time_factor = np.maximum(0, (time_of_day - 6) * (np.pi / 12))
    pv_sin = np.sin(pv_time_factor)
    # Verwende np.power statt ** um Warnungen zu vermeiden
    pv_shape = np.power(np.where(pv_sin > 0, pv_sin, 0), 1.5)
    # Stelle sicher, dass pv_shape ein numpy array ist
    pv_shape = np.array(pv_shape) * np.array(seasonal_factor)
    
    if pv_shape.max() > 0:
        normalized_pv = pv_shape / pv_shape.max()
    else:
        normalized_pv = np.zeros_like(pv_shape)
        
    pv_profile_kw = normalized_pv * potential_pv_kwp

    return pd.DataFrame({
        'consumption_kw': consumption_profile_kw,
        'production_kw': pv_profile_kw
    }, index=timestamps)

# --- Autarkie-Simulator ---

def calculate_community_autarky(community_buildings_df, all_profiles):
    """
    (Schnell) Berechnet den Autarkie-Score für einen Cluster.
    `all_profiles` wird ignoriert (kann None sein), da Profile neu generiert werden.
    """
    
    # 1. Profile für Simulation generieren
    all_profiles_sim = {}
    if community_buildings_df.empty:
        return 0.0, 0, 0

    first_building_id = None
    
    for _, row in community_buildings_df.iterrows():
        building_id = row['building_id']
        if first_building_id is None:
            first_building_id = building_id
            
        all_profiles_sim[building_id] = generate_mock_profiles(
            row['annual_consumption_kwh'],
            row['potential_pv_kwp']
        )

    if first_building_id is None:
         return 0.0, 0, 0

    # Holen Sie sich einen gültigen Index aus dem ersten Profil
    # Verwende .values um nur die Werte zu verwenden, nicht den DatetimeIndex
    first_profile = all_profiles_sim[first_building_id]
    num_points = len(first_profile)
    
    # Erstelle Series mit numerischem Index statt DatetimeIndex
    # Dies vermeidet "Index does not support mutable operations" Fehler
    community_consumption = pd.Series(0.0, index=range(num_points))
    community_production = pd.Series(0.0, index=range(num_points))
    
    # Verwende .values um sicherzustellen, dass wir über die Werte iterieren, nicht den Index
    for building_id in community_buildings_df['building_id'].values:
        profile = all_profiles_sim[building_id]
        # Verwende .values um nur die numerischen Werte zu bekommen
        community_consumption += pd.Series(profile['consumption_kw'].values, index=range(num_points))
        community_production += pd.Series(profile['production_kw'].values, index=range(num_points))
        
    net_load_kw = community_consumption - community_production
    energy_kwh = net_load_kw * 0.25
    grid_import_kwh = energy_kwh[energy_kwh > 0].sum()
    total_consumption_kwh = community_consumption.sum() * 0.25
    
    if total_consumption_kwh == 0: return 0.0, 0, 0
        
    autarky_score = (total_consumption_kwh - grid_import_kwh) / total_consumption_kwh
    return autarky_score, total_consumption_kwh, (community_production.sum() * 0.25)


# --- Cluster-Analyse ---

def get_cluster_info(community_df, cluster_id):
    """
    (Schnell) Hilfsfunktion, um die Details eines Clusters für das API-Response vorzubereiten.
    Fügt Koordinaten der Mitglieder hinzu.
    """
    autarky_score, total_consumption, total_production = calculate_community_autarky(
        community_df, None # Profile werden in der Fkt. neu generiert
    )
    
    # Extrahiere Koordinaten für die Kartenvisualisierung
    members = []
    for _, row in community_df.iterrows():
        members.append({
            'building_id': row['building_id'],
            'lat': row['lat'],
            'lon': row['lon']
        })

    return {
        'community_id': int(cluster_id) if isinstance(cluster_id, (int, np.integer)) else cluster_id,
        'num_members': len(community_df),
        'building_ids': list(community_df['building_id']), # Behalten für Logik
        'members': members, # NEU: Für Karten-Pins
        'autarky_percent': autarky_score * 100,
        'total_consumption_mwh': total_consumption / 1000,
        'total_production_mwh': total_production / 1000,
    }

def find_optimal_communities(building_data_df, radius_meters=150, min_community_size=3):
    """
    (Langsam) Main ML function (DBSCAN + Simulation).
    Gibt die gerankten Ergebnisse UND den DataFrame mit Cluster-Zuweisungen zurück.
    """
    if building_data_df.empty or len(building_data_df) < min_community_size:
        print(f"[ML] Zu wenig Daten für Clustering (min. {min_community_size} benötigt, {len(building_data_df)} vorhanden).")
        if not building_data_df.empty:
            # Erstelle einen neuen DataFrame, um Index-Probleme zu vermeiden
            result_df = pd.DataFrame(building_data_df.to_dict('records'))
            result_df['cluster'] = -1
            return [], result_df
        return [], building_data_df
        
    print(f"[ML] Starte ML-Clustering (DBSCAN) für {len(building_data_df)} Gebäude...")
    
    # Erstelle einen komplett neuen DataFrame, um Index-Probleme zu vermeiden
    # Dies stellt sicher, dass wir einen normalen numerischen Index haben
    working_df = pd.DataFrame(building_data_df.to_dict('records'))
    
    coords = working_df[['lat', 'lon']].values
    coords_rad = np.radians(coords)
    
    earth_radius_m = 6371e3
    eps_rad = radius_meters / earth_radius_m

    # Führe DBSCAN mit Haversine-Distanz (echte Erddistanz) aus
    db = DBSCAN(eps=eps_rad, min_samples=min_community_size, algorithm='ball_tree', metric='haversine').fit(coords_rad)
    
    working_df['cluster'] = db.labels_
    
    num_clusters = len(set(db.labels_)) - (1 if -1 in db.labels_ else 0)
    print(f"[ML] DBSCAN fand {num_clusters} potenzielle Gemeinschaften.")

    print("[ML] Simuliere Autarkie für jeden Cluster...")
    results = []
    for cluster_id in set(db.labels_):
        if cluster_id == -1: continue # Rauscht-Cluster (isolierte Gebäude)
            
        # Erstelle einen neuen DataFrame statt copy(), um Index-Probleme zu vermeiden
        cluster_mask = working_df['cluster'] == cluster_id
        community_buildings_df = pd.DataFrame(working_df[cluster_mask].to_dict('records'))
        
        # Ruft die aktualisierte Funktion auf
        cluster_info = get_cluster_info(community_buildings_df, cluster_id)
        results.append(cluster_info)

    # Sortiere nach höchster Autarkie
    ranked_results = sorted(results, key=lambda x: x['autarky_percent'], reverse=True)
    
    # Stelle sicher, dass building_id im Ergebnis-DataFrame erhalten bleibt
    # Verwende working_df, aber stelle sicher, dass alle ursprünglichen Spalten vorhanden sind
    result_df = working_df.copy()
    
    # Wenn building_id nicht im working_df ist, aber im ursprünglichen DataFrame war,
    # müssen wir es wieder hinzufügen
    if 'building_id' not in result_df.columns and 'building_id' in building_data_df.columns:
        # Versuche building_id basierend auf den anderen Spalten zu matchen
        building_data_df_reset = building_data_df.reset_index(drop=True)
        if len(result_df) == len(building_data_df_reset):
            result_df['building_id'] = building_data_df_reset['building_id'].values
    
    return ranked_results, result_df


if __name__ == "__main__":
    print("Dieses Skript ist ein Modul und sollte von app.py importiert werden.")
    print("Es kann nicht direkt ausgeführt werden.")

