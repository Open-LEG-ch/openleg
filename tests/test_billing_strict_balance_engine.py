# SPDX-License-Identifier: AGPL-3.0-or-later
"""Standalone source pricing must never redistribute unequal VNB energy."""

from decimal import Decimal

import pytest

import billing_engine
from tests.test_vnb_billing_prices import _unbalanced, _vnb_summary


@pytest.mark.parametrize("model", ["proportional", "einfach"])
@pytest.mark.parametrize("producer_kwh", ["0.499", "0.501"])
def test_direct_source_pricing_refuses_either_unequal_direction(model, producer_kwh):
    with pytest.raises(billing_engine.VnbBalancingPolicyRequired):
        _vnb_summary(_unbalanced(producer_kwh, "0.500"), distribution_model=model)


@pytest.mark.parametrize("model", ["proportional", "einfach"])
@pytest.mark.parametrize("producer_kwh", ["0.499", "0.501"])
def test_legacy_engine_allowance_cannot_authorize_unequal_energy(model, producer_kwh):
    # Removing the obsolete keyword is also a valid refusal of legacy callers.
    with pytest.raises(
        (billing_engine.VnbBalancingPolicyRequired, TypeError),
        match="balancing policy|unexpected keyword argument 'max_imbalance_ratio'",
    ):
        _vnb_summary(
            _unbalanced(producer_kwh, "0.500"),
            distribution_model=model,
            max_imbalance_ratio=Decimal("0.02"),
        )
