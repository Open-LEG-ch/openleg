# SPDX-License-Identifier: AGPL-3.0-or-later
"""Behavior contracts for the formation module's data and decisions.

These pins exist because mutation testing showed which contracts the suite
only implied: the document templates, the per-state next-step guidance, the
full status model, the savings arithmetic, and the neighbour cap. Each test
asserts exact values so a changed constant or operator fails loudly.
"""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from formation_wizard import (
    FormationStatus,
    _get_next_steps,
    _readiness_score,
    calculate_municipality_business_case,
    calculate_savings_estimate,
    get_community_status,
    get_contract_templates,
    get_formable_clusters,
)

MIN_SIZE = 3


def test_contract_templates_are_pinned_exactly():
    templates = get_contract_templates()

    assert templates["community_agreement"]["title"] == (
        "Lokale Elektrizitätsgemeinschaft - Gemeinschaftsvereinbarung"
    )
    assert templates["community_agreement"]["jurisdiction"] == "Kanton Zürich"
    assert templates["community_agreement"]["language"] == "de"
    assert templates["community_agreement"]["sections"] == [
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
    ]
    assert templates["participant_contract"]["title"] == "Teilnehmervertrag LEG"
    assert templates["participant_contract"]["jurisdiction"] == "Kanton Zürich"
    assert templates["participant_contract"]["language"] == "de"
    assert templates["participant_contract"]["sections"] == [
        "participant_info",
        "community_info",
        "obligations",
        "payment_terms",
        "termination",
    ]
    assert templates["dso_notification"]["title"] == (
        "Anmeldung Lokale Elektrizitätsgemeinschaft"
    )
    assert templates["dso_notification"]["recipient"] == "EKZ Verteilnetz AG"
    assert templates["dso_notification"]["form_id"] == "LEG-DSO-001"
    assert templates["dso_notification"]["sections"] == [
        "community_details",
        "participants",
        "grid_connection",
        "metering_setup",
        "start_date",
    ]


def test_contract_templates_follow_the_requested_jurisdiction_and_dso():
    templates = get_contract_templates(jurisdiction="Kanton Bern", dso_contact="BKW")

    assert templates["community_agreement"]["jurisdiction"] == "Kanton Bern"
    assert templates["participant_contract"]["jurisdiction"] == "Kanton Bern"
    assert templates["dso_notification"]["recipient"] == "BKW"


@pytest.mark.parametrize(
    ("status", "confirmed_count", "expected_steps"),
    [
        (
            FormationStatus.INTERESTED.value,
            1,
            ["Laden Sie mindestens 2 weitere Nachbarn ein."],
        ),
        (
            FormationStatus.INTERESTED.value,
            MIN_SIZE,
            ["Starten Sie den Gründungsprozess."],
        ),
        (
            FormationStatus.FORMATION_STARTED.value,
            MIN_SIZE,
            [
                "Erstellen Sie die rechtlichen Dokumente.",
                "Prüfen Sie die Gemeinschaftsvereinbarung.",
            ],
        ),
        (
            FormationStatus.DOCUMENTS_GENERATED.value,
            MIN_SIZE,
            [
                "Sammeln Sie die Unterschriften aller Mitglieder.",
                "Prüfen Sie die Teilnehmerverträge.",
            ],
        ),
        (
            FormationStatus.SIGNATURES_PENDING.value,
            MIN_SIZE,
            ["Melden Sie die LEG beim Netzbetreiber an."],
        ),
        (
            FormationStatus.DSO_SUBMITTED.value,
            MIN_SIZE,
            [
                "Warten Sie auf die Bewilligung durch den Netzbetreiber (bis zu 30 Tage)."
            ],
        ),
        (
            FormationStatus.DSO_APPROVED.value,
            MIN_SIZE,
            [
                "Legen Sie das Aktivierungsdatum fest.",
                "Richten Sie die Abrechnung ein.",
            ],
        ),
        (FormationStatus.ACTIVE.value, MIN_SIZE, []),
        (FormationStatus.REJECTED.value, MIN_SIZE, []),
    ],
)
def test_next_steps_are_pinned_per_state(status, confirmed_count, expected_steps):
    assert _get_next_steps(status, confirmed_count) == expected_steps


def test_readiness_score_defaults_an_unknown_state_to_zero():
    assert _readiness_score("never-heard-of-it", 0) == 0


def test_readiness_score_never_rewards_a_rejected_community():
    assert _readiness_score(FormationStatus.REJECTED.value, MIN_SIZE) == 0


def test_readiness_score_weights_each_known_state():
    assert _readiness_score(FormationStatus.DSO_APPROVED.value, MIN_SIZE) == 100
    assert _readiness_score(FormationStatus.DSO_APPROVED.value, 0) == 70


def _full_row():
    return {
        "community_id": "community-1",
        "name": "LEG Musterweg",
        "status": FormationStatus.DSO_SUBMITTED.value,
        "distribution_model": "proportional",
        "admin_building_id": "b-admin",
        "created_at": datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc),
        "formation_started_at": datetime(2026, 8, 2, 12, 0, 0, tzinfo=timezone.utc),
        "dso_submitted_at": datetime(2026, 8, 3, 12, 0, 0, tzinfo=timezone.utc),
        "members": [
            {"building_id": "b-admin", "status": "confirmed"},
            {"building_id": "b-2", "status": "confirmed"},
            {"building_id": "b-3", "status": "confirmed"},
            {"building_id": "b-4", "status": "invited"},
        ],
    }


def test_community_status_is_pinned_in_full():
    with patch(
        "database.fetch_community_with_members", return_value=_full_row()
    ) as read:
        status = get_community_status("community-1")

    read.assert_called_once_with("community-1")
    assert status == {
        "community_id": "community-1",
        "name": "LEG Musterweg",
        "status": "dso_submitted",
        "distribution_model": "proportional",
        "admin_building_id": "b-admin",
        "created_at": "2026-08-01T12:00:00+00:00",
        "formation_started_at": "2026-08-02T12:00:00+00:00",
        "dso_submitted_at": "2026-08-03T12:00:00+00:00",
        "member_count": {"total": 4, "confirmed": 3, "invited": 1},
        "readiness_score": 80,
        "members": _full_row()["members"],
        "documents": None,
        "next_steps": [
            "Warten Sie auf die Bewilligung durch den Netzbetreiber (bis zu 30 Tage)."
        ],
    }


def test_formable_clusters_cap_the_neighbour_list_at_ten():
    nearby = [
        {"building_id": f"b-{number}", "distance": float(number)}
        for number in range(12)
    ]
    with patch(
        "database.fetch_nearby_consenting_neighbours", return_value=nearby
    ) as read:
        clusters = get_formable_clusters("searcher", radius_meters=250)

    read.assert_called_once_with("searcher", 250)
    assert clusters[0]["potential_members"] == 13
    assert clusters[0]["ready_to_form"] is True
    assert clusters[0]["radius_meters"] == 250
    assert len(clusters[0]["nearby_buildings"]) == 10
    assert [row["building_id"] for row in clusters[0]["nearby_buildings"]] == [
        f"b-{number}" for number in range(10)
    ]


def test_consumer_savings_are_pinned_exactly():
    estimate = calculate_savings_estimate(
        consumption_kwh=4500, pv_kwp=0, community_size=5
    )

    assert estimate == {
        "annual_savings_chf": 135.0,
        "monthly_savings_chf": 11.25,
        "five_year_savings_chf": 675.0,
        "assumptions": {
            "grid_buy_price_rp": 25.0,
            "grid_sell_price_rp": 6.0,
            "leg_price_rp": 15.0,
            "community_size": 5,
            "solar_kwh_per_kwp": 950,
            "self_consumption_share_pct": 30.0,
        },
    }


def test_producer_savings_are_pinned_exactly():
    estimate = calculate_savings_estimate(
        consumption_kwh=4500, pv_kwp=10, community_size=5
    )

    assert estimate["annual_savings_chf"] == 1140.0
    assert estimate["monthly_savings_chf"] == 95.0
    assert estimate["five_year_savings_chf"] == 5700.0


def test_producer_savings_pin_the_capped_leg_sales_and_grid_credit():
    estimate = calculate_savings_estimate(
        consumption_kwh=100, pv_kwp=50, community_size=5
    )

    assert estimate == {
        "annual_savings_chf": 55.0,
        "monthly_savings_chf": 4.58,
        "five_year_savings_chf": 275.0,
        "assumptions": {
            "grid_buy_price_rp": 25.0,
            "grid_sell_price_rp": 6.0,
            "leg_price_rp": 15.0,
            "community_size": 5,
            "solar_kwh_per_kwp": 950,
            "self_consumption_share_pct": 30.0,
        },
    }


def test_a_tiny_producer_stays_in_the_producer_branch():
    estimate = calculate_savings_estimate(
        consumption_kwh=4500, pv_kwp=0.0005, community_size=5
    )

    assert estimate["annual_savings_chf"] == 0.06
    assert estimate["monthly_savings_chf"] == 0.0
    assert estimate["five_year_savings_chf"] == 0.29


def test_savings_are_rounded_to_two_decimals():
    estimate = calculate_savings_estimate(
        consumption_kwh=1234.5, pv_kwp=0, community_size=5
    )

    assert estimate["annual_savings_chf"] == 37.04
    assert estimate["monthly_savings_chf"] == 3.09
    assert estimate["five_year_savings_chf"] == 185.18


def test_municipality_business_case_is_pinned_on_its_first_year():
    case = calculate_municipality_business_case(
        bfs_number=261,
        num_legs=2,
        avg_community_size=10,
        avg_pv_kwp=10,
        avg_consumption_kwh=4500,
    )

    assert case["bfs_number"] == 261
    assert case["num_legs"] == 2
    assert case["total_households"] == 20
    assert case["annual_savings_per_household"] == 1140.0
    assert case["annual_total_savings"] == 22800.0
    assert case["projections"][0] == {
        "year": 1,
        "annual_total_chf": 22800.0,
        "cumulative_chf": 22800.0,
    }
    assert len(case["projections"]) == 10
    assert case["projections"][1]["annual_total_chf"] == 23256.0
    assert case["projections"][9]["cumulative_chf"] == 249653.64
    assert case["co2_reduction_total_kg"] == 729.6
    assert case["assumptions"] == {
        "grid_buy_price_rp": 25.0,
        "grid_sell_price_rp": 6.0,
        "leg_price_rp": 15.0,
        "community_size": 10,
        "solar_kwh_per_kwp": 950,
        "self_consumption_share_pct": 30.0,
    }


def test_municipality_business_case_defaults_to_its_configuration():
    case = calculate_municipality_business_case(bfs_number=261)

    assert case["num_legs"] == 5
    assert case["total_households"] == 50
    assert case["annual_savings_per_household"] == 3015.0
    assert case["annual_total_savings"] == 150750.0
    assert case["co2_reduction_total_kg"] == 5472.0


def test_municipality_business_case_rounds_year_aggregates_to_two_decimals():
    case = calculate_municipality_business_case(
        bfs_number=261,
        num_legs=1,
        avg_community_size=3,
        avg_pv_kwp=0,
        avg_consumption_kwh=1234.5,
    )

    assert case["annual_savings_per_household"] == 37.04
    assert case["annual_total_savings"] == 111.12
    assert case["projections"][0] == {
        "year": 1,
        "annual_total_chf": 111.12,
        "cumulative_chf": 111.12,
    }
    assert case["projections"][1]["annual_total_chf"] == 113.34


def test_co2_reduction_keeps_one_decimal():
    case = calculate_municipality_business_case(
        bfs_number=261, num_legs=1, avg_pv_kwp=10.003
    )

    assert case["co2_reduction_total_kg"] == 364.9


def test_municipality_business_case_handles_no_planned_communities():
    case = calculate_municipality_business_case(bfs_number=261, num_legs=0)

    assert case["total_households"] == 0
    assert case["annual_total_savings"] == 0
    assert case["co2_reduction_total_kg"] == 0
    assert len(case["projections"]) == 10
    assert all(
        projection["annual_total_chf"] == 0 and projection["cumulative_chf"] == 0
        for projection in case["projections"]
    )
