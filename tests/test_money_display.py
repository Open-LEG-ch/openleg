# SPDX-License-Identifier: AGPL-3.0-or-later
"""Money display policies shared by billing callers."""

import pytest


@pytest.mark.parametrize(
    "value", [None, "broken", "NaN", "sNaN", "Infinity", "-Infinity"]
)
def test_dashboard_battery_money_is_unreadable(value):
    from dashboard import _battery_view

    view = _battery_view(
        {"annual_cost_chf": value, "capacity_kwh": 10, "shares": {}}, []
    )

    assert view["annual_cost_display"] == "CHF Unlesbar"


@pytest.mark.parametrize(
    "value,display",
    [
        ("0", "0.00"),
        ("-12.34", "-12.34"),
        ("2.665", "2.66"),
        ("-2.665", "-2.66"),
        ("123456789012345678901234567890.125", "123456789012345678901234567890.12"),
    ],
)
def test_dashboard_battery_money_keeps_context_rounding(value, display):
    from dashboard import _battery_view

    view = _battery_view(
        {"annual_cost_chf": value, "capacity_kwh": 10, "shares": {}}, []
    )

    assert view["annual_cost_display"] == f"CHF {display}"


def test_format_amount_rejects_values_that_exceed_billing_precision():
    from decimal import ROUND_HALF_UP

    from money_display import InvalidMoneyAmount, format_amount

    with pytest.raises(InvalidMoneyAmount):
        format_amount("1e100", rounding=ROUND_HALF_UP)


def test_dashboard_money_keeps_the_active_decimal_rounding_context():
    from decimal import ROUND_UP, localcontext

    from dashboard import _display_gross_chf, _money_display

    with localcontext() as context:
        context.rounding = ROUND_UP
        assert _money_display("2.661") == "2.67"
        assert _display_gross_chf({"gross_chf": "2.661"}) == ("2.67", False)
