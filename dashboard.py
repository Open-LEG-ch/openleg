# SPDX-License-Identifier: AGPL-3.0-or-later
"""Dashboard readiness verb."""

import database as db


def readiness(building_id: str, *, city_id=None, app_base_url: str = "") -> dict:
    """Compute readiness view for one building.

    Returns a dict with user, readiness_score, checks, neighbor_count,
    referral_link and error. On missing / unknown building_id, user is None
    and error is set.
    """
    if not building_id:
        return {"error": "Kein Profil angegeben.", "user": None}

    user = db.get_building_for_dashboard(building_id)
    if not user:
        return {"error": "Profil nicht gefunden.", "user": None}

    score = 0
    checks = []
    if user.get("verified"):
        score += 25
        checks.append(("E-Mail bestätigt", True))
    else:
        checks.append(("E-Mail bestätigt", False))
    if user.get("annual_consumption_kwh"):
        score += 25
        checks.append(("Verbrauchsdaten hinterlegt", True))
    else:
        checks.append(("Verbrauchsdaten hinterlegt", False))
    if user.get("share_with_utility"):
        score += 25
        checks.append(("EVU-Einwilligung erteilt", True))
    else:
        checks.append(("EVU-Einwilligung erteilt", False))
    if user.get("share_with_neighbors"):
        score += 25
        checks.append(("Nachbar-Einwilligung erteilt", True))
    else:
        checks.append(("Nachbar-Einwilligung erteilt", False))

    neighbor_count = 0
    lat = user.get("lat")
    lon = user.get("lon")
    if lat and lon:
        neighbor_count = db.get_neighbor_count_near(
            float(lat), float(lon), city_id=city_id
        )

    referral_link = ""
    ref_code = db.get_referral_code(building_id)
    if ref_code:
        referral_link = f"{app_base_url}/?ref={ref_code}"

    return {
        "user": user,
        "readiness_score": score,
        "checks": checks,
        "neighbor_count": neighbor_count,
        "referral_link": referral_link,
        "error": None,
    }
