# SPDX-License-Identifier: AGPL-3.0-or-later
"""Finite decimal validation and formatting, without changing stored amounts."""

from decimal import Decimal


class InvalidMoneyAmount(ValueError):
    """An amount cannot be represented as a finite decimal display."""


def finite_decimal(value: object) -> Decimal:
    """Parse a finite amount, rejecting missing and malformed values."""
    try:
        amount = Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError) as exc:
        raise InvalidMoneyAmount("Invalid money amount") from exc
    if not amount.is_finite():
        raise InvalidMoneyAmount("Invalid money amount")
    return amount


def format_amount(
    value: object,
    places: int = 2,
    *,
    rounding: str | None = None,
    scale: Decimal = Decimal(1),
) -> str:
    """Format using the caller's rounding policy.

    With explicit rounding, retain billing's scale-then-quantize behavior.
    Otherwise use Decimal's fixed-point formatter and its current context,
    which also preserves the dashboard's support for large amounts.
    """
    amount = finite_decimal(value)
    try:
        if rounding is not None:
            quantum = Decimal(1).scaleb(-places)
            amount = (amount * scale).quantize(quantum, rounding=rounding)
        elif scale != 1:
            amount *= scale
        if not amount.is_finite():
            raise InvalidMoneyAmount("Invalid money amount")
        return format(amount, f".{places}f")
    except (ArithmeticError, TypeError, ValueError) as exc:
        raise InvalidMoneyAmount("Invalid money amount") from exc
