# SPDX-License-Identifier: AGPL-3.0-or-later
"""Dashboard readiness verb."""

import math
from urllib.parse import urlencode

import database as db
import formation_documents
import formation_wizard
import security_utils

_PROFILE_EXPORT_FIELDS = (
    "building_id",
    "email",
    "phone",
    "address",
    "lat",
    "lon",
    "plz",
    "building_type",
    "annual_consumption_kwh",
    "potential_pv_kwp",
    "registered_at",
    "verified",
    "verified_at",
    "user_type",
    "city_id",
    "share_with_neighbors",
    "share_with_utility",
    "updates_opt_in",
    "consent_version",
)


def leg_dashboard_location(community_id: str) -> str:
    """Build the dashboard redirect target with untrusted values encoded.

    urlencode keeps the location a relative /leg/dashboard path no matter
    what the caller passes (no protocol-relative //host, no fragments).
    """
    return "/leg/dashboard?" + urlencode({"cid": community_id})


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


def _with_german_labels(community: dict) -> dict:
    """Return a shallow copy with German display labels for rendering."""
    labeled = dict(community)
    labeled["status_label"] = formation_wizard.FORMATION_STATUS_LABELS.get(
        labeled.get("status"), "Status wird geprüft"
    )
    labeled["distribution_model_label"] = (
        formation_wizard.DISTRIBUTION_MODEL_LABELS.get(
            labeled.get("distribution_model"),
            "Verteilmodell wird geprüft",
        )
    )
    labeled_members = []
    for member in labeled.get("members") or []:
        labeled_member = dict(member)
        labeled_member["role_label"] = formation_wizard.MEMBER_ROLE_LABELS.get(
            labeled_member.get("role"), "Rolle wird geprüft"
        )
        labeled_member["status_label"] = formation_wizard.MEMBER_STATUS_LABELS.get(
            labeled_member.get("status"), "Status wird geprüft"
        )
        labeled_members.append(labeled_member)
    labeled["members"] = labeled_members
    return labeled


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
        "community": _with_german_labels(status),
        "viewer_building_id": building_id,
        "is_admin": member.get("role") == "admin",
        "leg_documents": db.list_leg_documents(community_id),
        "correspondence": db.list_correspondence(community_id),
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


def leg_invite_by_email(community_id: str, building_id: str, invite_email: str) -> dict:
    """Invite a known profile by email without exposing profile identifiers.

    A valid address always gets the same response. This prevents the operator
    dashboard from becoming an email-enumeration surface.
    """
    if not _require_role(community_id, building_id, "admin"):
        return {"error": "Nur die Administration kann einladen."}
    valid, normalized, error = security_utils.validate_email_address(invite_email)
    if not valid or not normalized:
        return {"error": error or "Bitte geben Sie eine gültige E-Mail-Adresse ein."}

    for profile in db.get_building_by_email(normalized) or []:
        invite_building_id = (profile.get("building_id") or "").strip()
        if not invite_building_id or invite_building_id == building_id:
            continue
        formation_wizard.invite_member(
            db, community_id, invite_building_id, building_id
        )
        break
    return {"error": None}


def export_profile(building_id: str) -> dict:
    """Return an explicit, JSON-safe export of one resident-owned profile."""
    profile = db.get_building(building_id) or {}
    exported = {}
    for field in _PROFILE_EXPORT_FIELDS:
        value = profile.get(field)
        if isinstance(value, float) and not math.isfinite(value):
            continue
        if value is None or isinstance(value, (str, int, float, bool)):
            exported[field] = value
        elif hasattr(value, "isoformat"):
            exported[field] = value.isoformat()
        elif field in {"lat", "lon", "annual_consumption_kwh", "potential_pv_kwp"}:
            numeric_value = float(value)
            if math.isfinite(numeric_value):
                exported[field] = numeric_value
    return exported


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


def leg_generate_documents(community_id: str, building_id: str) -> dict:
    """Generate the complete document bundle through its domain seam."""
    return formation_documents.generate(community_id, building_id)


def leg_document_for_member(doc_id: int, building_id: str):
    """Return a stored document only if building_id belongs to its community."""
    doc = db.get_leg_document(doc_id)
    if not doc:
        return None
    status = formation_wizard.get_community_status(db, doc["community_id"])
    if not status:
        return None
    is_member = any(m["building_id"] == building_id for m in status["members"] or [])
    return doc if is_member else None


def leg_log_correspondence(
    community_id: str,
    building_id: str,
    direction: str,
    channel: str,
    counterparty: str,
    subject: str,
    notes: str = "",
    attachment_filename: str = "",
    attachment_data: bytes | None = None,
) -> dict:
    """Append a ledger entry; any confirmed or invited member may log."""
    status = formation_wizard.get_community_status(db, community_id)
    if not status or not any(
        m["building_id"] == building_id for m in status["members"] or []
    ):
        return {"error": "Kein Zugriff."}

    if attachment_data is not None:
        if not attachment_filename.lower().endswith(".pdf"):
            return {"error": "Anhänge müssen PDF-Dateien sein."}
        if not attachment_data.startswith(b"%PDF-"):
            return {"error": "Der Anhang ist keine gültige PDF-Datei."}
        if len(attachment_data) > 2 * 1024 * 1024:
            return {"error": "Der PDF-Anhang darf höchstens 2 MB gross sein."}

    entry_id = db.log_correspondence(
        community_id=community_id,
        direction=direction,
        channel=channel,
        counterparty=(counterparty or "").strip(),
        subject=(subject or "").strip(),
        notes=(notes or "").strip(),
        logged_by=building_id,
        attachment_filename=attachment_filename,
        attachment_mime="application/pdf" if attachment_data else "",
        attachment_data=attachment_data,
    )
    if entry_id is None:
        return {"error": "Eintrag ungültig (Richtung oder Kanal unbekannt)."}
    return {"error": None, "entry_id": entry_id}


def leg_correspondence_attachment(
    entry_id: int, community_id: str, building_id: str
) -> dict | None:
    """Return a correspondence attachment only to a current LEG member."""
    status = formation_wizard.get_community_status(db, community_id)
    if not status or not any(
        member["building_id"] == building_id for member in status["members"] or []
    ):
        return None
    return db.get_correspondence_attachment(entry_id, community_id)


def leg_demo_overview() -> dict:
    """Fake, click-through LEG operator dashboard data for demos."""
    community = {
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
    }
    community["next_steps"] = formation_wizard._get_next_steps(
        community["status"], community["member_count"]["confirmed"]
    )
    return {
        "error": None,
        "viewer_building_id": "demo-building",
        "is_admin": True,
        "community": _with_german_labels(community),
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


def update_profile(
    building_id: str,
    *,
    annual_consumption_kwh: str | None = None,
    potential_pv_kwp: str | None = None,
    share_with_utility: bool = False,
    share_with_neighbors: bool = False,
) -> dict:
    """Validate and delegate a dashboard profile update.

    Returns ``{"error": None}`` on success or ``{"error": "..."}``
    when validation fails.  Invalid input never reaches the store.
    """
    try:
        consumption = (
            float(annual_consumption_kwh)
            if annual_consumption_kwh not in (None, "")
            else None
        )
    except (TypeError, ValueError):
        return {"error": "Bitte geben Sie einen gültigen Jahresverbrauch ein."}

    if (
        consumption is None
        or not math.isfinite(consumption)
        or consumption <= 0
        or consumption > 9_999_999_999.99
    ):
        return {"error": "Bitte geben Sie einen gültigen Jahresverbrauch ein."}

    try:
        pv = float(potential_pv_kwp) if potential_pv_kwp not in (None, "") else None
    except (TypeError, ValueError):
        return {"error": "Bitte geben Sie eine gültige Solarleistung ein."}

    if pv is not None and (not math.isfinite(pv) or pv < 0 or pv > 999_999.99):
        return {"error": "Bitte geben Sie eine gültige Solarleistung ein."}

    saved = db.update_dashboard_profile(
        building_id,
        annual_consumption_kwh=consumption,
        potential_pv_kwp=pv,
        share_with_utility=share_with_utility,
        share_with_neighbors=share_with_neighbors,
    )
    if not saved:
        return {
            "error": "Das Energieprofil konnte nicht gespeichert werden. "
            "Bitte versuchen Sie es erneut."
        }
    return {"error": None}
