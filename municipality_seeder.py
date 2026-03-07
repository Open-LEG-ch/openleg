"""Bulk municipality seeding for OpenLEG.
Seeds all Swiss municipalities from Energie Reporter open data.
"""

import logging
import re
from typing import Dict

import database as db
import public_data

logger = logging.getLogger(__name__)

# 26-canton map: kanton name -> code
KANTON_CODE_MAP = {
    'Zürich': 'ZH',
    'Bern': 'BE',
    'Luzern': 'LU',
    'Uri': 'UR',
    'Schwyz': 'SZ',
    'Obwalden': 'OW',
    'Nidwalden': 'NW',
    'Glarus': 'GL',
    'Zug': 'ZG',
    'Fribourg': 'FR',
    'Solothurn': 'SO',
    'Basel-Stadt': 'BS',
    'Basel-Landschaft': 'BL',
    'Schaffhausen': 'SH',
    'Appenzell Ausserrhoden': 'AR',
    'Appenzell Innerrhoden': 'AI',
    'St. Gallen': 'SG',
    'Graubünden': 'GR',
    'Aargau': 'AG',
    'Thurgau': 'TG',
    'Ticino': 'TI',
    'Vaud': 'VD',
    'Valais': 'VS',
    'Neuchâtel': 'NE',
    'Genève': 'GE',
    'Jura': 'JU',
    # Short codes map to themselves
    'ZH': 'ZH',
    'BE': 'BE',
    'LU': 'LU',
    'UR': 'UR',
    'SZ': 'SZ',
    'OW': 'OW',
    'NW': 'NW',
    'GL': 'GL',
    'ZG': 'ZG',
    'FR': 'FR',
    'SO': 'SO',
    'BS': 'BS',
    'BL': 'BL',
    'SH': 'SH',
    'AR': 'AR',
    'AI': 'AI',
    'SG': 'SG',
    'GR': 'GR',
    'AG': 'AG',
    'TG': 'TG',
    'TI': 'TI',
    'VD': 'VD',
    'VS': 'VS',
    'NE': 'NE',
    'GE': 'GE',
    'JU': 'JU',
}

# Switzerland bounding box defaults
CH_MAP_BOUNDS = {
    'map_center_lat': 46.8182,
    'map_center_lon': 8.2275,
    'map_zoom': 12,
    'map_bounds_sw': [45.8, 5.9],
    'map_bounds_ne': [47.8, 10.5],
}


def provision_tenant_for_municipality(bfs, name, kanton, subdomain, dso_name=None):
    """Create white_label_configs tenant for a municipality. Idempotent: skips if exists.

    Returns True if created, False if skipped.
    """
    existing = db.get_tenant_by_territory(subdomain)
    if existing:
        logger.debug(f'[SEEDER] Tenant {subdomain} already exists, skipping')
        return False

    kanton_code = KANTON_CODE_MAP.get(kanton, kanton[:2].upper() if kanton else 'ZH')

    config = {
        'city_name': name,
        'kanton': kanton,
        'kanton_code': kanton_code,
        'platform_name': f'OpenLEG {name}',
        'brand_prefix': 'OpenLEG',
        'active': True,
        'primary_color': '#0d9488',
        'secondary_color': '#f59e0b',
        'contact_email': 'hallo@openleg.ch',
        **CH_MAP_BOUNDS,
    }

    if dso_name:
        config['utility_name'] = dso_name
        config['dso_contact'] = f'{dso_name} Verteilnetz AG'

    db.upsert_tenant(subdomain, config)
    logger.info(f'[SEEDER] Provisioned tenant: {subdomain} ({name}, {kanton_code})')
    return True


def _slugify(name: str) -> str:
    """Convert municipality name to URL-safe subdomain slug."""
    slug = name.lower().strip()
    replacements = {
        'ä': 'ae',
        'ö': 'oe',
        'ü': 'ue',
        'é': 'e',
        'è': 'e',
        'ê': 'e',
        'à': 'a',
        'â': 'a',
        'î': 'i',
        'ô': 'o',
        'û': 'u',
        'ç': 'c',
        'ñ': 'n',
        'ß': 'ss',
    }
    for k, v in replacements.items():
        slug = slug.replace(k, v)
    slug = re.sub(r'[^a-z0-9]+', '-', slug).strip('-')
    return slug or 'unknown'


def seed_all_municipalities(kanton_filter: str = None, provision_tenants: bool = False) -> Dict:
    """Seed municipalities from Energie Reporter into DB.

    Args:
        kanton_filter: Only seed municipalities in this kanton.
        provision_tenants: Also create white_label_configs tenant rows.

    Returns: {"seeded": int, "failed": int, "skipped": int, "tenants_created": int}
    """
    er_data = public_data.fetch_energie_reporter()
    if not er_data:
        logger.warning('[SEEDER] No Energie Reporter data, nothing to seed')
        return {'seeded': 0, 'failed': 0, 'skipped': 0, 'tenants_created': 0}

    seeded = 0
    failed = 0
    skipped = 0
    tenants_created = 0

    for entry in er_data:
        bfs = entry.get('bfs_number')
        name = entry.get('name', '')
        kanton = entry.get('kanton', '')

        if not bfs or not name:
            skipped += 1
            continue

        if kanton_filter and kanton != kanton_filter:
            skipped += 1
            continue

        subdomain = _slugify(name)

        # Save municipality record
        muni_id = db.save_municipality(
            bfs_number=bfs,
            name=name,
            kanton=kanton,
            subdomain=subdomain,
        )

        if not muni_id:
            failed += 1
            continue

        # Compute energy transition score
        score = public_data.compute_energy_transition_score(entry)

        # Save profile
        profile = {
            'bfs_number': bfs,
            'name': name,
            'kanton': kanton,
            'solar_potential_pct': entry.get('solar_potential_pct'),
            'ev_share_pct': entry.get('ev_share_pct'),
            'renewable_heating_pct': entry.get('renewable_heating_pct'),
            'electricity_consumption_mwh': entry.get('electricity_consumption_mwh'),
            'renewable_production_mwh': entry.get('renewable_production_mwh'),
            'energy_transition_score': score,
            'data_sources': {'energie_reporter': True},
        }
        db.save_municipality_profile(profile)
        seeded += 1

        if provision_tenants:
            created = provision_tenant_for_municipality(bfs, name, kanton, subdomain)
            if created:
                tenants_created += 1

    logger.info(f'[SEEDER] Done: {seeded} seeded, {failed} failed, {skipped} skipped, {tenants_created} tenants')
    return {'seeded': seeded, 'failed': failed, 'skipped': skipped, 'tenants_created': tenants_created}
