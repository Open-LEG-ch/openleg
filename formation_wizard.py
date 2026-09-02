# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Formation Wizard Module for OpenLEG
Handles LEG community formation workflow, document generation, and status tracking.

Formation rules, readiness, next steps, and status assembly live here; the SQL
lives in store/formation and is reached through the database re-exports.
"""

import logging
from enum import Enum

import database as db

logger = logging.getLogger(__name__)


class FormationStatus(Enum):
    """LEG formation workflow states."""

    INTERESTED = "interested"  # User registered, not in community
    INVITED = "invited"  # Invited to join community
    CONFIRMED = "confirmed"  # Confirmed participation
    FORMATION_STARTED = "formation_started"  # Community formation initiated
    DOCUMENTS_GENERATED = "documents_generated"  # Contracts ready
    SIGNATURES_PENDING = "signatures_pending"  # Waiting for signatures
    DSO_SUBMITTED = "dso_submitted"  # DSO notification sent
    DSO_APPROVED = "dso_approved"  # DSO approved
    ACTIVE = "active"  # Community operational
    REJECTED = "rejected"  # Formation failed/rejected


class DistributionModel(Enum):
    """Energy distribution models."""

    SIMPLE = "simple"  # Equal distribution
    PROPORTIONAL = "proportional"  # Based on consumption/production
    CUSTOM = "custom"  # Custom rules


# German display labels for the LEG dashboard.
# Machine values stay ASCII; these maps are the single source of truth for
# user-facing Schweizer Hochdeutsch text.
FORMATION_STATUS_LABELS = {
    FormationStatus.INTERESTED.value: "Interessiert",
    FormationStatus.INVITED.value: "Eingeladen",
    FormationStatus.CONFIRMED.value: "Bestätigt",
    FormationStatus.FORMATION_STARTED.value: "Gründung gestartet",
    FormationStatus.DOCUMENTS_GENERATED.value: "Dokumente erstellt",
    FormationStatus.SIGNATURES_PENDING.value: "Unterschriften ausstehend",
    FormationStatus.DSO_SUBMITTED.value: "Netzbetreiber informiert",
    FormationStatus.DSO_APPROVED.value: "Netzbetreiber hat bewilligt",
    FormationStatus.ACTIVE.value: "Aktiv",
    FormationStatus.REJECTED.value: "Abgelehnt",
}

DISTRIBUTION_MODEL_LABELS = {
    DistributionModel.SIMPLE.value: "Gleichverteilt",
    DistributionModel.PROPORTIONAL.value: "Nach Verbrauch und Erzeugung",
    DistributionModel.CUSTOM.value: "Individuelle Regeln",
}

MEMBER_ROLE_LABELS = {
    "admin": "Verwaltung",
    "member": "Mitglied",
}

MEMBER_STATUS_LABELS = {
    "invited": "Eingeladen",
    "confirmed": "Bestätigt",
    "rejected": "Abgelehnt",
}


# Formation configuration
FORMATION_CONFIG = {
    "min_community_size": 3,
    "max_community_size": 50,
    "formation_fee_chf": 0,
    "servicing_fee_monthly_chf": 0,
    "dso_response_days": 30,
    "signature_timeout_days": 14,
}

_FORMATION_PROCESS_POINTS = {
    FormationStatus.INTERESTED.value: 0,
    FormationStatus.INVITED.value: 0,
    FormationStatus.CONFIRMED.value: 0,
    FormationStatus.FORMATION_STARTED.value: 0,
    FormationStatus.DOCUMENTS_GENERATED.value: 30,
    FormationStatus.SIGNATURES_PENDING.value: 30,
    FormationStatus.DSO_SUBMITTED.value: 50,
    FormationStatus.DSO_APPROVED.value: 70,
    FormationStatus.ACTIVE.value: 70,
    FormationStatus.REJECTED.value: 0,
}

DEFAULT_GRID_BUY_PRICE_RP = 25.0
DEFAULT_GRID_SELL_PRICE_RP = 6.0
DEFAULT_LEG_PRICE_RP = 15.0
DEFAULT_SELF_CONSUMPTION_SHARE_PCT = 30.0
DEFAULT_SOLAR_KWH_PER_KWP = 950


def get_contract_templates(
    jurisdiction="Kanton Zürich", dso_contact="EKZ Verteilnetz AG"
):
    """Return contract templates parameterized by jurisdiction and DSO."""
    return {
        "community_agreement": {
            "title": "Lokale Elektrizitätsgemeinschaft - Gemeinschaftsvereinbarung",
            "jurisdiction": jurisdiction,
            "language": "de",
            "sections": [
                "parties",
                "purpose",
                "territory",
                "participation",
                "distribution_model",
                "metering",
                "billing",
                "liability",
                "termination",
                "governing_law",
            ],
        },
        "participant_contract": {
            "title": "Teilnehmervertrag LEG",
            "jurisdiction": jurisdiction,
            "language": "de",
            "sections": [
                "participant_info",
                "community_info",
                "obligations",
                "payment_terms",
                "termination",
            ],
        },
        "dso_notification": {
            "title": "Anmeldung Lokale Elektrizitätsgemeinschaft",
            "recipient": dso_contact,
            "form_id": "LEG-DSO-001",
            "sections": [
                "community_details",
                "participants",
                "grid_connection",
                "metering_setup",
                "start_date",
            ],
        },
    }


# Default templates (backward compatible)
CONTRACT_TEMPLATES = get_contract_templates()


def create_community(
    name: str,
    admin_building_id: str,
    distribution_model: str = "simple",
    description: str = "",
) -> dict | None:
    """
    Create a new LEG community.

    Args:
        name: Community name
        admin_building_id: Building ID of the community admin
        distribution_model: Distribution model (simple/proportional/custom)
        description: Optional description

    Returns:
        Community dict or None if failed
    """
    community_id = db.create_community_record(
        name, admin_building_id, distribution_model, description
    )
    if not community_id:
        return None

    return {
        "community_id": community_id,
        "name": name,
        "admin_building_id": admin_building_id,
        "distribution_model": distribution_model,
        "status": FormationStatus.INTERESTED.value,
        "member_count": 1,
    }


def invite_member(community_id: str, building_id: str, invited_by: str) -> bool:
    """
    Invite a building to join a community.

    Args:
        community_id: Community ID
        building_id: Building ID to invite
        invited_by: Building ID of inviter

    Returns:
        True if successful
    """
    return db.insert_invited_member(community_id, building_id, invited_by)


def confirm_membership(community_id: str, building_id: str) -> bool:
    """
    Confirm membership after invitation.

    Args:
        community_id: Community ID
        building_id: Building ID confirming

    Returns:
        True if successful
    """
    return db.confirm_invited_member(community_id, building_id)


def start_formation(community_id: str) -> bool:
    """
    Start the formal LEG formation process.

    Args:
        community_id: Community ID

    Returns:
        True if successful
    """
    count = db.count_confirmed_members(community_id)
    if count is None or count < FORMATION_CONFIG["min_community_size"]:
        logger.warning(
            f"[FORMATION] Community {community_id} has only {count} members, need {FORMATION_CONFIG['min_community_size']}"
        )
        return False

    return db.mark_formation_started(community_id)


def submit_to_dso(community_id: str) -> bool:
    """
    Submit DSO notification for a community.

    Args:
        community_id: Community ID

    Returns:
        True if submitted successfully
    """
    return db.submit_community_to_dso(community_id)


def _member_counts(members: list[dict]) -> dict:
    """Count members by status."""
    confirmed = sum(1 for member in members if member["status"] == "confirmed")
    total = len(members)
    return {
        "total": total,
        "confirmed": confirmed,
        "invited": total - confirmed,
    }


def _readiness_score(status: str, confirmed_count: int) -> int:
    """Weighted formation progress for the current state and member base."""
    score = _FORMATION_PROCESS_POINTS.get(status, 0)
    if (
        status != FormationStatus.REJECTED.value
        and confirmed_count >= FORMATION_CONFIG["min_community_size"]
    ):
        score += 30
    return score


def _iso(value) -> str | None:
    """ISO timestamp for a raw column value."""
    return value.isoformat() if value else None


def get_community_status(community_id: str) -> dict | None:
    """
    Get full status of a community formation.

    Args:
        community_id: Community ID

    Returns:
        Community status dict or None
    """
    try:
        row = db.fetch_community_with_members(community_id)
        if not row:
            return None

        members = row["members"] or []
        counts = _member_counts(members)

        return {
            "community_id": row["community_id"],
            "name": row["name"],
            "status": row["status"],
            "distribution_model": row["distribution_model"],
            "admin_building_id": row["admin_building_id"],
            "created_at": _iso(row["created_at"]),
            "formation_started_at": _iso(row["formation_started_at"]),
            "dso_submitted_at": _iso(row["dso_submitted_at"]),
            "member_count": counts,
            "readiness_score": _readiness_score(row["status"], counts["confirmed"]),
            "members": members,
            "documents": None,
            "next_steps": _get_next_steps(row["status"], counts["confirmed"]),
        }
    except Exception:
        logger.exception("[FORMATION] Error assembling community status")
        return None


def _get_next_steps(status: str, confirmed_count: int) -> list[str]:
    """Get recommended next steps based on status (Schweizer Hochdeutsch)."""
    steps = []

    if status == FormationStatus.INTERESTED.value:
        if confirmed_count < FORMATION_CONFIG["min_community_size"]:
            steps.append(
                f"Laden Sie mindestens {FORMATION_CONFIG['min_community_size'] - confirmed_count} weitere Nachbarn ein."
            )
        else:
            steps.append("Starten Sie den Gründungsprozess.")

    elif status == FormationStatus.FORMATION_STARTED.value:
        steps.append("Erstellen Sie die rechtlichen Dokumente.")
        steps.append("Prüfen Sie die Gemeinschaftsvereinbarung.")

    elif status == FormationStatus.DOCUMENTS_GENERATED.value:
        steps.append("Sammeln Sie die Unterschriften aller Mitglieder.")
        steps.append("Prüfen Sie die Teilnehmerverträge.")

    elif status == FormationStatus.SIGNATURES_PENDING.value:
        steps.append("Melden Sie die LEG beim Netzbetreiber an.")

    elif status == FormationStatus.DSO_SUBMITTED.value:
        steps.append(
            "Warten Sie auf die Bewilligung durch den Netzbetreiber (bis zu 30 Tage)."
        )

    elif status == FormationStatus.DSO_APPROVED.value:
        steps.append("Legen Sie das Aktivierungsdatum fest.")
        steps.append("Richten Sie die Abrechnung ein.")

    return steps


def get_user_communities(building_id: str) -> list[dict]:
    """
    Get all communities a user is part of.

    Args:
        building_id: Building ID

    Returns:
        List of community dicts
    """
    return db.fetch_user_communities(building_id) or []


def get_formable_clusters(building_id: str, radius_meters: int = 150) -> list[dict]:
    """
    Get clusters that are ready for formation (have enough members).

    Args:
        building_id: Building ID to center search
        radius_meters: Search radius

    Returns:
        List of formable cluster dicts
    """
    nearby = db.fetch_nearby_consenting_neighbours(building_id, radius_meters)
    if nearby is None:
        return []

    if len(nearby) >= FORMATION_CONFIG["min_community_size"] - 1:
        return [
            {
                "potential_members": len(nearby) + 1,  # +1 for user
                "nearby_buildings": nearby[:10],  # Top 10
                "radius_meters": radius_meters,
                "ready_to_form": len(nearby) + 1
                >= FORMATION_CONFIG["min_community_size"],
            }
        ]
    return []


def calculate_municipality_business_case(
    bfs_number: int,
    num_legs: int = 5,
    avg_community_size: int = 10,
    avg_pv_kwp: float = 30,
    avg_consumption_kwh: float = 4500,
) -> dict:
    """
    Calculate business case for a municipality's LEG program.
    Returns aggregate projections for multiple LEGs.
    """
    per_household = calculate_savings_estimate(
        avg_consumption_kwh, avg_pv_kwp, avg_community_size
    )
    annual_per_hh = per_household.get("annual_savings_chf", 0)
    total_households = num_legs * avg_community_size

    projections = []
    cumulative = 0
    for year in range(1, 11):
        year_savings = annual_per_hh * total_households * (1.02 ** (year - 1))
        cumulative += year_savings
        projections.append(
            {
                "year": year,
                "annual_total_chf": round(year_savings, 2),
                "cumulative_chf": round(cumulative, 2),
            }
        )

    co2_per_leg = avg_pv_kwp * DEFAULT_SOLAR_KWH_PER_KWP * 0.3 * 0.128  # kg CO2
    return {
        "bfs_number": bfs_number,
        "num_legs": num_legs,
        "total_households": total_households,
        "annual_savings_per_household": round(annual_per_hh, 2),
        "annual_total_savings": round(annual_per_hh * total_households, 2),
        "projections": projections,
        "co2_reduction_total_kg": round(co2_per_leg * num_legs, 1),
        "assumptions": per_household.get("assumptions", {}),
    }


def calculate_savings_estimate(
    consumption_kwh: float,
    pv_kwp: float,
    community_size: int,
    solar_kwh_per_kwp: int = DEFAULT_SOLAR_KWH_PER_KWP,
) -> dict:
    """
    Calculate estimated savings for a household in a LEG.

    Args:
        consumption_kwh: Annual consumption in kWh
        pv_kwp: PV capacity in kWp
        community_size: Number of households in community

    Returns:
        Savings estimate dict
    """
    # Estimate production (800-1050 kWh/kWp/year in Switzerland, varies by region)
    estimated_production = pv_kwp * solar_kwh_per_kwp if pv_kwp else 0
    self_consumption_share = DEFAULT_SELF_CONSUMPTION_SHARE_PCT / 100

    # Simple model: share production within community
    if estimated_production > 0:
        # Producer scenario
        self_consumption = min(
            consumption_kwh, estimated_production * self_consumption_share
        )
        leg_sales = min(
            estimated_production - self_consumption,
            consumption_kwh * (community_size - 1),
        )
        grid_sales = estimated_production - self_consumption - leg_sales
        grid_purchase = max(0, consumption_kwh - self_consumption)

        # Revenue/cost
        leg_revenue = leg_sales * DEFAULT_LEG_PRICE_RP / 100  # Convert Rp to CHF
        grid_revenue = grid_sales * DEFAULT_GRID_SELL_PRICE_RP / 100
        grid_cost = grid_purchase * DEFAULT_GRID_BUY_PRICE_RP / 100

        net_cost = grid_cost - leg_revenue - grid_revenue

        # Without LEG
        without_leg_cost = (consumption_kwh * DEFAULT_GRID_BUY_PRICE_RP / 100) - (
            estimated_production * DEFAULT_GRID_SELL_PRICE_RP / 100
        )

        annual_savings = without_leg_cost - net_cost
    else:
        # Consumer scenario
        leg_purchase = consumption_kwh * self_consumption_share
        grid_purchase = consumption_kwh * (1 - self_consumption_share)

        with_leg_cost = (leg_purchase * DEFAULT_LEG_PRICE_RP / 100) + (
            grid_purchase * DEFAULT_GRID_BUY_PRICE_RP / 100
        )
        without_leg_cost = consumption_kwh * DEFAULT_GRID_BUY_PRICE_RP / 100

        annual_savings = without_leg_cost - with_leg_cost

    return {
        "annual_savings_chf": round(annual_savings, 2),
        "monthly_savings_chf": round(annual_savings / 12, 2),
        "five_year_savings_chf": round(annual_savings * 5, 2),
        "assumptions": {
            "grid_buy_price_rp": DEFAULT_GRID_BUY_PRICE_RP,
            "grid_sell_price_rp": DEFAULT_GRID_SELL_PRICE_RP,
            "leg_price_rp": DEFAULT_LEG_PRICE_RP,
            "community_size": community_size,
            "solar_kwh_per_kwp": solar_kwh_per_kwp,
            "self_consumption_share_pct": DEFAULT_SELF_CONSUMPTION_SHARE_PCT,
        },
    }
