# SPDX-License-Identifier: AGPL-3.0-or-later
"""Dashboard readiness verb."""

import database as db
import formation_wizard


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
    if lat is not None and lon is not None:
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


def leg_overview(community_id: str, building_id: str) -> dict:
    """Operator view of one community, gated on membership.

    Same capability-URL model as the resident dashboard: the caller must
    present a building_id that is a member of the community. Non-members
    get the error view, never another community's data.
    """
    if not community_id or not building_id:
        return {"error": "Kein Zugriff.", "community": None}

    status = formation_wizard.get_community_status(db, community_id)
    if not status:
        return {"error": "LEG nicht gefunden.", "community": None}

    member = next(
        (m for m in status["members"] or [] if m["building_id"] == building_id),
        None,
    )
    if not member:
        return {"error": "Kein Zugriff.", "community": None}

    return {
        "error": None,
        "community": status,
        "viewer_building_id": building_id,
        "is_admin": member.get("role") == "admin",
    }


def leg_demo_overview() -> dict:
    """Fake, click-through LEG operator dashboard data for demos."""
    return {
        "error": None,
        "viewer_building_id": "demo-building",
        "is_admin": True,
        "community": {
            "community_id": "demo-leg",
            "name": "LEG Musterweg",
            "status": "formation_started",
            "distribution_model": "proportional",
            "member_count": {"total": 5, "confirmed": 4, "invited": 1},
            "readiness_score": 60,
            "members": [
                {
                    "building_id": "demo-building",
                    "role": "admin",
                    "status": "confirmed",
                    "address": "Musterweg 1, 5400 Baden",
                },
                {
                    "building_id": "demo-2",
                    "role": "member",
                    "status": "confirmed",
                    "address": "Musterweg 3, 5400 Baden",
                },
                {
                    "building_id": "demo-3",
                    "role": "member",
                    "status": "confirmed",
                    "address": "Musterweg 5, 5400 Baden",
                },
                {
                    "building_id": "demo-4",
                    "role": "member",
                    "status": "confirmed",
                    "address": "Musterweg 7, 5400 Baden",
                },
                {
                    "building_id": "demo-5",
                    "role": "member",
                    "status": "invited",
                    "address": "Musterweg 9, 5400 Baden",
                },
            ],
            "documents": None,
            "next_steps": [
                "Generate legal documents",
                "Review community agreement",
            ],
        },
    }


def demo_readiness() -> dict:
    """Fake, click-through dashboard data for demos."""
    return {
        "user": {
            "building_id": "demo-building",
            "address": "Mellingerstrasse 12, 5400 Baden",
            "annual_consumption_kwh": 4200,
            "potential_pv_kwp": 8.5,
            "referral_count": 4,
        },
        "readiness_score": 75,
        "checks": [
            ("E-Mail bestätigt", True),
            ("Verbrauchsdaten hinterlegt", True),
            ("EVU-Einwilligung erteilt", False),
            ("Nachbar-Einwilligung erteilt", True),
        ],
        "neighbor_count": 18,
        "referral_link": "https://openleg.ch/?ref=DEMO-LEG",
        "error": None,
    }
