# SPDX-License-Identifier: AGPL-3.0-or-later
"""Municipality profile/pilot read models (issue #209).

Deep module owning tariff/solar/value-gap assembly for the public profile
and pilot case-study pages, plus the value-gap API calculation, so routes
stay thin request/response shells.
"""

import database as db
import public_data
from ranking import Ranking

PROFILE_TARIFF_YEAR = 2026
PILOT_MUNICIPALITIES = {"baden": 4021}


def _first_h4_tariff(bfs, year=None):
    tariffs = db.get_elcom_tariffs(bfs, year=year)
    h4 = next((t for t in tariffs if str(t.get("category", "")).startswith("H4")), None)
    return tariffs, h4


def _value_gap_for_tariff(h4, grid_reduction_pct):
    if not h4:
        return None
    return public_data.compute_leg_value_gap(h4, grid_reduction_pct=grid_reduction_pct)


def value_gap(bfs, *, year=PROFILE_TARIFF_YEAR, grid_reduction_pct=40.0):
    _tariffs, h4 = _first_h4_tariff(bfs, year=year)
    return _value_gap_for_tariff(h4, grid_reduction_pct)


def _format_rp_kwh(value):
    if value is None:
        return None
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return None


def _profile_seo(name, h4_tariff):
    h4_total = _format_rp_kwh(h4_tariff.get("total_rp_kwh")) if h4_tariff else None
    year = (h4_tariff or {}).get("year")
    year_part = f" {year}" if year else ""

    if h4_total:
        title = (
            f"Stromtarif {name}{year_part}: {h4_total} Rp/kWh, Solar und LEG | OpenLEG"
        )
        description = (
            f"Stromtarif {name}: {h4_total} Rp/kWh im H4-Profil. "
            "OpenLEG zeigt Solarnutzung und LEG-Potenzial für die Gemeinde."
        )
    else:
        title = f"Stromtarif {name}: Solar und LEG | OpenLEG"
        description = (
            f"Stromtarif {name}: OpenLEG zeigt Solarnutzung, "
            "Energieprofil und LEG-Potenzial für die Gemeinde."
        )

    return title, description


def _profile_jsonld(profile, bfs, h4_tariff, site_url, canonical_url):
    name = (profile.get("name") or "").strip()
    kanton = (profile.get("kanton") or "").strip().upper()[:2]
    graph = [
        {
            "@type": "Place",
            "name": name,
            "identifier": str(bfs),
            "containedInPlace": {
                "@type": "AdministrativeArea",
                "name": kanton,
            },
            "url": canonical_url,
        },
        {
            "@type": "BreadcrumbList",
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "position": 1,
                    "name": "Gemeindeverzeichnis",
                    "item": f"{site_url}/gemeinde/verzeichnis",
                },
                {
                    "@type": "ListItem",
                    "position": 2,
                    "name": name,
                    "item": canonical_url,
                },
            ],
        },
    ]

    operator_name = str((h4_tariff or {}).get("operator_name") or "").strip()
    if operator_name:
        graph.append(
            {
                "@type": "Organization",
                "name": operator_name,
                "description": "Verteilnetzbetreiber",
            }
        )

    return {"@context": "https://schema.org", "@graph": graph}


def profile_context(bfs, *, site_url):
    profile = db.get_municipality_profile(bfs)
    if not profile:
        return None

    site_url = site_url.rstrip("/")

    tariffs, h4 = _first_h4_tariff(bfs, year=PROFILE_TARIFF_YEAR)
    solar = db.get_sonnendach_municipal(bfs)
    gap = _value_gap_for_tariff(h4, grid_reduction_pct=40.0)

    solar_score, solar_over_100 = Ranking.capped_score(profile.get("pv_score_pct"))
    if solar_score is None and profile.get("solar_potential_pct") is not None:
        solar_score = round(float(profile["solar_potential_pct"]), 1)

    league_chips = []
    improvement = None
    already_top = False
    leaders = []
    if profile.get("pv_score_pct") is not None:
        ranking = Ranking.load()
        league_chips = ranking.league_chips(profile)
        improvement = ranking.improvement_target(profile)
        size_rank = ranking.size_league_rank(profile)
        already_top = bool(size_rank and size_rank["quartile"] == Ranking.TOP_QUARTILE)
        leaders = ranking.leaders(profile.get("kanton"), exclude_bfs=bfs)

    pilot_slug = next(
        (slug for slug, pilot_bfs in PILOT_MUNICIPALITIES.items() if pilot_bfs == bfs),
        None,
    )

    name = (profile.get("name") or "").strip()
    canonical_url = f"{site_url}/gemeinde/profil/{bfs}"
    seo_title, seo_description = _profile_seo(name, h4)
    jsonld = _profile_jsonld(profile, bfs, h4, site_url, canonical_url)

    leg_entries = db.list_registry_entries(q=name) if name else []

    return {
        "profile": profile,
        "leg_entries": leg_entries,
        "tariffs": tariffs,
        "solar": solar,
        "value_gap": gap,
        "h4_tariff": h4,
        "solar_score": solar_score,
        "solar_over_100": solar_over_100,
        "league_chips": league_chips,
        "improvement": improvement,
        "already_top": already_top,
        "leaders": leaders,
        "site_url": site_url,
        "share_base": site_url,
        "canonical_url": canonical_url,
        "seo_title": seo_title,
        "seo_description": seo_description,
        "jsonld": jsonld,
        "pilot_slug": pilot_slug,
    }


def pilot_context(slug, *, site_url):
    bfs = PILOT_MUNICIPALITIES.get(slug)
    if bfs is None:
        return None

    profile = db.get_municipality_profile(bfs)
    if not profile:
        return None

    site_url = site_url.rstrip("/")

    # No year filter: get_elcom_tariffs orders year DESC, so the first H4
    # entry is always the latest available tariff.
    _tariffs, h4 = _first_h4_tariff(bfs)
    solar = db.get_sonnendach_municipal(bfs)
    gap = _value_gap_for_tariff(h4, grid_reduction_pct=40.0)

    place_id = f"#place-{bfs}"
    json_ld = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "Article",
                "headline": f"Fallstudie: LEG-Potenzial in {profile.get('name')}",
                "author": {"@type": "Organization", "name": "OpenLEG"},
                "publisher": {"@type": "Organization", "name": "OpenLEG"},
                "about": {"@id": place_id},
            },
            {
                "@id": place_id,
                "@type": "Place",
                "name": profile.get("name"),
                "identifier": str(bfs),
                "containedInPlace": {
                    "@type": "AdministrativeArea",
                    "name": profile.get("kanton"),
                },
            },
        ],
    }

    return {
        "profile": profile,
        "bfs": bfs,
        "slug": slug,
        "h4": h4,
        "solar": solar,
        "value_gap": gap,
        "json_ld": json_ld,
        "site_url": site_url,
        "canonical_path": f"/pilotgemeinde/{slug}",
    }
