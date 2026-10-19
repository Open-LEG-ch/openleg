# SPDX-License-Identifier: AGPL-3.0-or-later
"""Municipality profile/pilot read models (issue #209).

Deep module owning tariff/solar/value-gap assembly for the public profile
and pilot case-study pages, plus the value-gap API calculation, so routes
stay thin request/response shells.

Gemeindeprofil cache (#527): the assembled context is one cache unit per
municipality. Die Frische ist durch Veracity begrenzt, nicht durch Vorliebe:
der erfolgreiche Refresh invalidiert die Einheit ueber den gemeinsamen Cache
(in demselben Transaktionsfenster), und die TTL ist nur ein Rueckhalt, der
die Staerke begrenzt, falls die Invalidierung selbst scheitert. Ein Cache
ohne Treffer ist nie ein Fehler: Unavailability degradiert zu direkten
Lesungen.
"""

from copy import deepcopy
from decimal import Decimal

import cache
import database as db
import public_data
from i18n import gettext as _
from ranking import Ranking

PROFILE_TARIFF_YEAR = 2026
PILOT_MUNICIPALITIES = {"baden": 4021}

# Rueckhalt-TTL (#527): die Invalidierung im Refresh ist die eigentliche
# Frische-Garantie (gemeinsamer Cache, prozessuebergreifend). 300 Sekunden
# begrenzen den Schaden, falls eine Invalidierung scheitert, und bleiben
# weit unter jeder Plausibilitaetsschwelle fuer Tarifdaten.
MUNICIPALITY_PROFILE_TTL_SECONDS = 300
_PROFILE_CACHE_PREFIX = "municipality-profile"

_PROFILE_CACHE_METRICS = {"hits": 0, "misses": 0, "invalidations": 0}


def profile_cache_metrics():
    """Observable cache behaviour, so the claimed win is measurable."""
    return dict(_PROFILE_CACHE_METRICS)


def _reset_profile_cache_metrics_for_tests():
    _PROFILE_CACHE_METRICS.update(hits=0, misses=0, invalidations=0)


def _profile_cache_key(bfs, site_url):
    return f"{_PROFILE_CACHE_PREFIX}:{bfs}:{site_url}"


def invalidate_profile_cache(bfs):
    """Drop every cache unit of one municipality (all site URLs)."""
    cache.cache_clear_prefix(f"{_PROFILE_CACHE_PREFIX}:{bfs}:")
    _PROFILE_CACHE_METRICS["invalidations"] += 1


class _ContextEncoder:
    """Round-trips Decimal and datetime faithfully; strings would break the
    Jinja format filters and silently change every number's type."""

    @staticmethod
    def default(value):
        from datetime import date
        from datetime import datetime as dt

        if isinstance(value, Decimal):
            return {"__t": "dec", "v": str(value)}
        if isinstance(value, dt):
            return {"__t": "dt", "v": value.isoformat()}
        if isinstance(value, date):
            return {"__t": "date", "v": value.isoformat()}
        raise TypeError(f"unserialisable in profile cache: {type(value)!r}")

    @staticmethod
    def hook(tagged):
        from datetime import date
        from datetime import datetime as dt

        marker = tagged.get("__t") if isinstance(tagged, dict) else None
        if marker == "dec":
            return Decimal(tagged["v"])
        if marker == "dt":
            return dt.fromisoformat(tagged["v"])
        if marker == "date":
            return date.fromisoformat(tagged["v"])
        return tagged

    @classmethod
    def dumps(cls, context):
        import json

        return json.dumps(context, default=cls.default)

    @classmethod
    def loads(cls, raw):
        import json

        return json.loads(raw, object_hook=cls.hook)


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


def _profile_seo(name, h4_tariff, *, translate=True):
    gettext = (
        _
        if translate
        else lambda message, **values: message % values if values else message
    )
    h4_total = _format_rp_kwh(h4_tariff.get("total_rp_kwh")) if h4_tariff else None
    year = (h4_tariff or {}).get("year")
    year_part = f" {year}" if year else ""

    if h4_total:
        title = gettext(
            "Stromtarif %(name)s%(year_part)s: %(h4_total)s Rp/kWh, Solar und LEG | OpenLEG",
            name=name,
            year_part=year_part,
            h4_total=h4_total,
        )
        description = gettext(
            "Stromtarif %(name)s: %(h4_total)s Rp/kWh im H4-Profil. "
            "OpenLEG zeigt Solarnutzung und LEG-Potenzial für die Gemeinde.",
            name=name,
            h4_total=h4_total,
        )
    else:
        title = gettext("Stromtarif %(name)s: Solar und LEG | OpenLEG", name=name)
        description = gettext(
            "Stromtarif %(name)s: OpenLEG zeigt Solarnutzung, "
            "Energieprofil und LEG-Potenzial für die Gemeinde.",
            name=name,
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
    """Assembled profile read model, cached per municipality (#527)."""
    site_url = site_url.rstrip("/")
    key = _profile_cache_key(bfs, site_url)
    try:
        raw = cache.cache_get(key)
    except Exception:
        raw = None
    if raw is not None:
        _PROFILE_CACHE_METRICS["hits"] += 1
        return _localize_profile_context(_ContextEncoder.loads(raw))

    _PROFILE_CACHE_METRICS["misses"] += 1
    context = _build_profile_context(bfs, site_url=site_url)
    if context is not None:
        cache.cache_set(
            key, _ContextEncoder.dumps(context), ttl=MUNICIPALITY_PROFILE_TTL_SECONDS
        )
    return _localize_profile_context(context)


def _localize_profile_context(context):
    """Keep the shared cache language-neutral; translate only after retrieval."""
    if context is None:
        return None
    localized = dict(context)
    profile = context["profile"]
    name = (profile.get("name") or "").strip()
    if "seo_title" in context:
        localized["seo_title"], localized["seo_description"] = _profile_seo(
            name, context.get("h4_tariff")
        )
    if "jsonld" in context:
        localized["jsonld"] = deepcopy(context["jsonld"])
        for item in localized["jsonld"].get("@graph", []):
            if item.get("@type") == "BreadcrumbList":
                item["itemListElement"][0]["name"] = _("Gemeindeverzeichnis")
            if item.get("@type") == "Organization":
                item["description"] = _("Verteilnetzbetreiber")
    if "league_chips" in context:
        localized["league_chips"] = [
            {**chip, "label": _("Kanton %(kanton)s", kanton=chip["label"][7:])}
            if chip["label"].startswith("Kanton ")
            else dict(chip)
            for chip in context["league_chips"]
        ]
    return localized


def _build_profile_context(bfs, *, site_url):
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
    seo_title, seo_description = _profile_seo(name, h4, translate=False)
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
                "headline": _(
                    "Fallstudie: LEG-Potenzial in %(name)s", name=profile.get("name")
                ),
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
