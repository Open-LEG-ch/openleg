# SPDX-License-Identifier: AGPL-3.0-or-later
"""The source precision grid must not depend on Decimal context or exponent size."""

from decimal import localcontext
from fractions import Fraction

import pytest

from sdat_e66 import exact_e66_kwh


@pytest.mark.parametrize("precision", [2, 28, 50])
@pytest.mark.parametrize(
    "value,expected",
    [
        ("1.001", Fraction(1001, 1000)),
        ("1.00000000000000000000000000000", Fraction(1)),
        ("0E-999999999", Fraction(0)),
        ("99999999.999", Fraction(99999999999, 1000)),
    ],
)
def test_exact_source_quantities_are_independent_of_decimal_context(
    precision, value, expected
):
    with localcontext() as context:
        context.prec = precision
        assert exact_e66_kwh(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "1.00000000000000000000000000001",
        "0.0001",
        "1E-999999999",
        "1E+999999999",
        "100000000",
        "NaN",
        "Infinity",
        "-0.001",
    ],
)
def test_unrepresentable_source_quantities_are_refused_before_fraction_expansion(value):
    with pytest.raises(ValueError):
        exact_e66_kwh(value)
