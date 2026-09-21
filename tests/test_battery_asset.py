# SPDX-License-Identifier: AGPL-3.0-or-later
"""Validation and draft-assembly contract for the Quartierakku."""

from copy import deepcopy
from decimal import Decimal

import pytest

import battery_asset

PARTICIPANTS = ("building-a", "building-b")


def _form(**overrides):
    form = {
        "name": "Quartierakku",
        "participant_id": "battery-a",
        "capacity_kwh": "45",
        "annual_cost_chf": "960",
        "share_building-a": "25",
        "share_building-b": "75",
    }
    form.update(overrides)
    return form


def test_valid_form_produces_the_asset_with_decimal_shares():
    result = battery_asset.validate_battery_form(_form(), PARTICIPANTS)

    assert result["errors"] == {}
    asset = result["asset"]
    assert asset["name"] == "Quartierakku"
    assert asset["capacity_kwh"] == Decimal(45)
    assert asset["annual_cost_chf"] == Decimal(960)
    assert asset["shares"] == [
        ("building-a", Decimal(25)),
        ("building-b", Decimal(75)),
    ]


def test_zero_cost_is_allowed_but_negative_capacity_is_not():
    result = battery_asset.validate_battery_form(
        _form(annual_cost_chf="0", capacity_kwh="-1"), PARTICIPANTS
    )
    assert "capacity_kwh" in result["errors"]

    accepted = battery_asset.validate_battery_form(
        _form(annual_cost_chf="0"), PARTICIPANTS
    )
    assert accepted["errors"] == {}


@pytest.mark.parametrize("value", ["0", "-1", "abc", "nan", "100001"])
def test_out_of_domain_capacity_is_refused(value):
    result = battery_asset.validate_battery_form(
        _form(capacity_kwh=value), PARTICIPANTS
    )
    assert result["asset"] is None
    assert "capacity_kwh" in result["errors"]


@pytest.mark.parametrize("value", ["-0.01", "abc", "nan", "100001"])
def test_out_of_domain_annual_cost_is_refused(value):
    result = battery_asset.validate_battery_form(
        _form(annual_cost_chf=value), PARTICIPANTS
    )
    assert result["asset"] is None
    assert "annual_cost_chf" in result["errors"]


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"share_building-a": "-1"}, "share_building-a"),
        ({"share_building-b": "abc"}, "share_building-b"),
    ],
)
def test_negative_or_garbled_shares_are_refused(overrides, field):
    result = battery_asset.validate_battery_form(_form(**overrides), PARTICIPANTS)
    assert result["asset"] is None
    assert field in result["errors"]


def test_shares_must_sum_to_exactly_100_percent():
    result = battery_asset.validate_battery_form(
        _form(**{"share_building-b": "70"}), PARTICIPANTS
    )
    assert result["asset"] is None
    assert "shares" in result["errors"]
    assert "100 Prozent" in result["errors"]["shares"]


def test_a_missing_share_input_is_refused():
    form = _form()
    del form["share_building-b"]
    result = battery_asset.validate_battery_form(form, PARTICIPANTS)
    assert result["asset"] is None
    assert "share_building-b" in result["errors"]


def test_a_community_without_confirmed_members_cannot_configure_shares():
    result = battery_asset.validate_battery_form(_form(), [])
    assert result["asset"] is None
    assert "shares" in result["errors"]


def test_blank_name_is_refused():
    result = battery_asset.validate_battery_form(_form(name="  "), PARTICIPANTS)
    assert result["asset"] is None
    assert "name" in result["errors"]


def test_blank_battery_participant_is_refused():
    result = battery_asset.validate_battery_form(
        _form(participant_id="  "), PARTICIPANTS
    )
    assert result["asset"] is None
    assert "participant_id" in result["errors"]


def test_draft_block_splits_the_annual_cost_at_cent_precision():
    asset = battery_asset.validate_battery_form(
        _form(
            annual_cost_chf="1000",
            **{"share_building-a": "12.5", "share_building-b": "87.5"},
        ),
        PARTICIPANTS,
    )["asset"]

    block = battery_asset.draft_block(asset, PARTICIPANTS)

    assert block["share_amounts_chf"]["building-a"] == "125.00"
    assert block["share_amounts_chf"]["building-b"] == "875.00"


def test_draft_block_does_not_demand_a_share_for_the_battery_participant():
    asset = battery_asset.validate_battery_form(_form(), PARTICIPANTS)["asset"]

    block = battery_asset.draft_block(asset, (*PARTICIPANTS, "battery-a"))

    assert block["participant_id"] == "battery-a"


def test_draft_block_refuses_a_participant_without_a_share():
    asset = battery_asset.validate_battery_form(_form(), PARTICIPANTS)["asset"]

    with pytest.raises(battery_asset.BatteryAssetError):
        battery_asset.draft_block(asset, (*PARTICIPANTS, "building-c"))


def test_draft_block_refuses_an_incomplete_asset():
    with pytest.raises(battery_asset.BatteryAssetError):
        battery_asset.draft_block({"name": "Quartierakku"}, PARTICIPANTS)


FROZEN_BLOCK = {
    "name": "Quartierakku",
    "participant_id": "battery-a",
    "capacity_kwh": "45",
    "annual_cost_chf": "960",
    "shares_pct": {"building-a": "25", "building-b": "75"},
    "share_amounts_chf": {"building-a": "240.00", "building-b": "720.00"},
}


def test_frozen_block_verifies_amounts_against_shares():
    assert (
        battery_asset.validate_frozen_block(deepcopy(FROZEN_BLOCK), PARTICIPANTS)
        == FROZEN_BLOCK
    )


def test_frozen_block_refuses_amounts_that_disagree_with_shares():
    block = deepcopy(FROZEN_BLOCK)
    block["share_amounts_chf"]["building-b"] = "700.00"

    with pytest.raises(battery_asset.BatteryAssetError):
        battery_asset.validate_frozen_block(block, PARTICIPANTS)


def test_frozen_block_refuses_shares_that_do_not_cover_the_billed_participants():
    block = {
        "name": "Quartierakku",
        "capacity_kwh": "45",
        "annual_cost_chf": "960",
        "shares_pct": {"building-a": "100"},
        "share_amounts_chf": {"building-a": "960.00"},
    }

    with pytest.raises(battery_asset.BatteryAssetError):
        battery_asset.validate_frozen_block(block, PARTICIPANTS)


def test_participant_share_returns_the_frozen_entry():
    block = {
        "name": "Quartierakku",
        "participant_id": "battery-a",
        "capacity_kwh": "45",
        "annual_cost_chf": "960",
        "shares_pct": {"building-a": "25", "building-b": "75"},
        "share_amounts_chf": {"building-a": "240.00", "building-b": "720.00"},
    }

    share = battery_asset.participant_share(block, "building-a")
    assert share == {
        "name": "Quartierakku",
        "capacity_kwh": "45",
        "annual_cost_chf": "960",
        "share_pct": "25",
        "share_amount_chf": "240.00",
    }


def test_participant_share_is_none_without_a_battery():
    assert battery_asset.participant_share(None, "building-a") is None


def test_participant_share_refuses_a_missing_participant():
    block = {
        "name": "Quartierakku",
        "participant_id": "battery-a",
        "capacity_kwh": "45",
        "annual_cost_chf": "960",
        "shares_pct": {"building-a": "100"},
        "share_amounts_chf": {"building-a": "960.00"},
    }

    with pytest.raises(battery_asset.BatteryAssetError):
        battery_asset.participant_share(block, "building-b")
