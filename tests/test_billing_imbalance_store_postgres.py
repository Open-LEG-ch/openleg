# SPDX-License-Identifier: AGPL-3.0-or-later
"""Per-period VNB imbalance releases and blocks on the real schema.

A release raises the imbalance tolerance for exactly one billing window, with
a reason and the person who decided it. A block records why the last run of a
window stopped, so the workspace can show the figures instead of a bare code.
"""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import psycopg2
import pytest

from store import billing
from tests.test_store_billing_postgres import billing_database  # noqa: F401

pytestmark = pytest.mark.integration

ZURICH = ZoneInfo("Europe/Zurich")
COMMUNITY = "community-pg"
AUGUST = (datetime(2026, 8, 1, tzinfo=ZURICH), datetime(2026, 9, 1, tzinfo=ZURICH))
SEPTEMBER = (
    datetime(2026, 9, 1, tzinfo=ZURICH),
    datetime(2026, 10, 1, tzinfo=ZURICH),
)
FIGURES = {
    "community_consumption_kwh": Decimal("1000.000000"),
    "community_production_kwh": Decimal("1004.000000"),
    "imbalance_kwh": Decimal("4.000000"),
    "imbalance_ratio": Decimal("0.004000"),
    "tolerance_ratio": Decimal("0.001"),
}


@pytest.fixture
def community(billing_database):  # noqa: F811
    with billing_database.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO communities (community_id, name) VALUES (%s, %s)",
            (COMMUNITY, "LEG Test"),
        )
    return billing_database


def test_a_window_without_a_release_has_none(community):
    assert billing.get_billing_imbalance_release(COMMUNITY, *AUGUST) is None


def test_the_newest_release_of_a_window_applies(community):
    billing.save_billing_imbalance_release(
        COMMUNITY, *AUGUST, Decimal("0.003"), "BKW Ticket 4711", "admin-a"
    )
    newest = billing.save_billing_imbalance_release(
        COMMUNITY, *AUGUST, Decimal("0.005"), "BKW bestätigt Rundung", "admin-b"
    )

    release = billing.get_billing_imbalance_release(COMMUNITY, *AUGUST)

    assert release["id"] == newest["id"]
    assert release["max_imbalance_ratio"] == Decimal("0.005")
    assert release["reason"] == "BKW bestätigt Rundung"
    assert release["released_by"] == "admin-b"
    assert release["released_at"] is not None
    assert billing.get_billing_imbalance_release(COMMUNITY, *SEPTEMBER) is None


@pytest.mark.parametrize("ratio", [Decimal(0), Decimal("0.020001")])
def test_the_schema_refuses_a_ratio_outside_the_released_range(community, ratio):
    with pytest.raises(billing.BillingStoreError):
        billing.save_billing_imbalance_release(
            COMMUNITY, *AUGUST, ratio, "zu hoch", "admin-a"
        )


def test_the_schema_refuses_a_release_without_a_reason(community):
    with pytest.raises(billing.BillingStoreError):
        billing.save_billing_imbalance_release(
            COMMUNITY, *AUGUST, Decimal("0.003"), "   ", "admin-a"
        )


def test_a_blocked_window_is_listed_with_its_figures_and_release(community):
    billing.record_billing_imbalance_block(COMMUNITY, *AUGUST, FIGURES)
    billing.record_billing_imbalance_block(
        COMMUNITY, *AUGUST, {**FIGURES, "tolerance_ratio": Decimal("0.003")}
    )
    billing.save_billing_imbalance_release(
        COMMUNITY, *AUGUST, Decimal("0.003"), "BKW Ticket 4711", "admin-a"
    )

    (block,) = billing.list_billing_imbalance_blocks(COMMUNITY)

    assert block["period_start"] == AUGUST[0]
    assert block["period_end"] == AUGUST[1]
    assert block["imbalance_ratio"] == Decimal("0.004000")
    # The latest run's figures replace the earlier ones.
    assert block["tolerance_ratio"] == Decimal("0.003")
    assert block["release_ratio"] == Decimal("0.003")
    assert block["release_reason"] == "BKW Ticket 4711"
    assert block["release_by"] == "admin-a"


def test_a_window_with_a_current_draft_is_no_longer_blocked(community):
    billing.record_billing_imbalance_block(COMMUNITY, *AUGUST, FIGURES)
    with community.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO billing_periods (community_id, period_start, period_end,
                status, input_fingerprint)
            VALUES (%s, %s, %s, 'draft', %s)
            """,
            (COMMUNITY, *AUGUST, "f" * 64),
        )

    assert billing.list_billing_imbalance_blocks(COMMUNITY) == []


def test_blocks_are_scoped_to_their_community(community):
    with community.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO communities (community_id, name) VALUES ('other', 'Andere')"
        )
    billing.record_billing_imbalance_block("other", *AUGUST, FIGURES)

    assert billing.list_billing_imbalance_blocks(COMMUNITY) == []


def test_blocks_require_a_known_community(billing_database):  # noqa: F811
    with pytest.raises((billing.BillingStoreError, psycopg2.Error)):
        billing.record_billing_imbalance_block("unknown", *AUGUST, FIGURES)


def test_a_block_keeps_an_unbounded_ratio(community):
    """A tiny consumption side makes the ratio arbitrarily large."""
    figures = {
        **FIGURES,
        "community_consumption_kwh": Decimal("0.000001"),
        "community_production_kwh": Decimal("10.000000"),
        "imbalance_kwh": Decimal("9.999999"),
        "imbalance_ratio": Decimal("9999999.000000"),
    }

    billing.record_billing_imbalance_block(COMMUNITY, *AUGUST, figures)

    (block,) = billing.list_billing_imbalance_blocks(COMMUNITY)
    assert block["imbalance_ratio"] == Decimal("9999999.000000")
