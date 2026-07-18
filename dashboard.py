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


def _require_role(community_id: str, building_id: str, role: str):
    """Return the member row if building_id has the given role, else None."""
    status = formation_wizard.get_community_status(db, community_id)
    if not status:
        return None
    return next(
        (
            m
            for m in status["members"] or []
            if m["building_id"] == building_id and m.get("role") == role
        ),
        None,
    )


def leg_create(name: str, building_id: str, distribution_model: str) -> dict:
    """Create a community with building_id as admin."""
    name = (name or "").strip()
    if not name or not building_id:
        return {"error": "Name und Profil sind erforderlich.", "community_id": None}
    if distribution_model not in ("simple", "proportional", "custom"):
        distribution_model = "simple"
    created = formation_wizard.create_community(
        db, name, building_id, distribution_model
    )
    if not created:
        return {
            "error": "LEG konnte nicht erstellt werden.",
            "community_id": None,
        }
    return {"error": None, "community_id": created["community_id"]}


def leg_invite(community_id: str, building_id: str, invite_building_id: str) -> dict:
    """Invite a building; only the community admin may invite."""
    if not _require_role(community_id, building_id, "admin"):
        return {"error": "Nur die Administration kann einladen."}
    if not invite_building_id:
        return {"error": "Kein Profil zum Einladen angegeben."}
    ok = formation_wizard.invite_member(
        db, community_id, invite_building_id, building_id
    )
    if not ok:
        return {"error": "Einladung nicht möglich (bereits Mitglied?)."}
    return {"error": None}


def leg_confirm(community_id: str, building_id: str) -> dict:
    """Confirm one's own invited membership."""
    if not community_id or not building_id:
        return {"error": "Kein Zugriff."}
    ok = formation_wizard.confirm_membership(db, community_id, building_id)
    if not ok:
        return {"error": "Keine offene Einladung gefunden."}
    return {"error": None}


def leg_start_formation(community_id: str, building_id: str) -> dict:
    """Start formal formation; only the community admin may start."""
    if not _require_role(community_id, building_id, "admin"):
        return {"error": "Nur die Administration kann die Gründung starten."}
    ok = formation_wizard.start_formation(db, community_id)
    if not ok:
        return {"error": "Gründung noch nicht möglich (genug bestätigte Mitglieder?)."}
    return {"error": None}


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
