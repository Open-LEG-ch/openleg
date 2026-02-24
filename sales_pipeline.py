"""Sales pipeline management for VNB outreach.

Manages the lifecycle of utility client leads from discovery to conversion.
Designed to be called by OpenClaw (LEA) via MCP tools.
"""

PIPELINE_STAGES = ["lead", "contacted", "demo", "trial", "paid", "churned"]

# Scoring weights
W_POPULATION = 0.30
W_SOLAR = 0.20
W_COMPETITION = 0.30
W_SMART_METER = 0.20


def is_valid_transition(from_status, to_status):
    """Check if a pipeline status transition is valid (forward only, or to churned)."""
    if to_status == "churned":
        return True
    try:
        from_idx = PIPELINE_STAGES.index(from_status)
        to_idx = PIPELINE_STAGES.index(to_status)
        return to_idx == from_idx + 1
    except ValueError:
        return False


def score_vnb(population, solar_potential_kwh, has_leghub, smart_meter_coverage):
    """Auto-score a VNB lead (0-100).

    Factors:
    - Population (larger = more LEG potential)
    - Solar yield (higher = better value gap)
    - Competition gap (no LEGHub = higher score)
    - Smart meter coverage (higher = easier onboarding)
    """
    # Population score: 0-100, log scale, cap at 200k
    import math
    pop_score = min(100, (math.log10(max(population, 1)) / math.log10(200000)) * 100)

    # Solar score: normalize 800-1200 kWh/kWp range to 0-100
    solar_score = min(100, max(0, (solar_potential_kwh - 700) / 5))

    # Competition: no LEGHub = 100, has LEGHub = 20
    comp_score = 20 if has_leghub else 100

    # Smart meter: direct percentage to score
    meter_score = min(100, smart_meter_coverage * 100)

    total = (
        pop_score * W_POPULATION
        + solar_score * W_SOLAR
        + comp_score * W_COMPETITION
        + meter_score * W_SMART_METER
    )
    return min(100, max(0, round(total, 1)))


def get_pipeline(entries, status_filter=None):
    """Filter pipeline entries by status."""
    if status_filter:
        return [e for e in entries if e.get("status") == status_filter]
    return entries


def update_pipeline_status(entry, new_status):
    """Validate and update pipeline entry status.

    Returns updated entry or raises ValueError.
    """
    current = entry.get("status", "lead")
    if not is_valid_transition(current, new_status):
        raise ValueError(f"Ungültiger Übergang: {current} -> {new_status}")
    entry["status"] = new_status
    return entry


def draft_outreach_email(vnb_name, population, value_gap_chf, solar_potential_kwh):
    """Generate personalized outreach email draft for a VNB.

    Returns: German email text as string.
    """
    city = vnb_name.replace("Stadtwerk ", "").replace("EW ", "").replace("Elektrizitätswerk ", "")

    return f"""Betreff: LEG-Lösung für {vnb_name}

Guten Tag

Die Lokalen Elektrizitätsgemeinschaften (LEG) sind seit 1. Januar 2026 gesetzlich verankert. \
Für {city} mit rund {population:,} Einwohnern sehen wir ein Einsparpotenzial von \
CHF {value_gap_chf:.0f} pro Haushalt und Jahr durch die Netznutzungsrabatte.

Mit einem Solarpotenzial von {solar_potential_kwh} kWh/kWp bietet {city} ideale \
Voraussetzungen für LEG-Gründungen. OpenLEG bietet eine schlüsselfertige Plattform: \
Anmeldung, Vertragsmanagement, 15-Minuten-Abrechnung und DSO-Schnittstelle.

Gerne zeige ich Ihnen in 20 Minuten, wie andere Gemeindewerke bereits LEGs betreiben.

Freundliche Grüsse
OpenLEG Team
openleg.ch"""


def get_pipeline_dashboard(entries):
    """Compute funnel metrics from pipeline entries.

    Returns dict with total, funnel counts, avg_score.
    """
    funnel = {stage: 0 for stage in PIPELINE_STAGES}
    scores = []

    for e in entries:
        status = e.get("status", "lead")
        if status in funnel:
            funnel[status] += 1
        score = e.get("score", 0)
        if score:
            scores.append(score)

    return {
        "total": len(entries),
        "funnel": funnel,
        "avg_score": round(sum(scores) / len(scores), 1) if scores else 0,
        "conversion_rate": round(funnel.get("paid", 0) / max(1, len(entries)) * 100, 1),
    }
