# SPDX-License-Identifier: AGPL-3.0-or-later
"""Membership contribution policy, independent of persistence and HTTP."""

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

ANNUAL_DUES_CHF = 50


def received_amount(value):
    try:
        amount = Decimal(value)
        if not amount.is_finite() or amount <= 0 or amount > ANNUAL_DUES_CHF:
            raise ValueError("Invalid received amount")
        if amount != amount.quantize(Decimal("0.01")):
            raise ValueError("Use CHF cents")
        return amount
    except (InvalidOperation, TypeError):
        raise ValueError("Invalid received amount") from None


def admission_dates(accepted_at):
    day = accepted_at.astimezone(ZoneInfo("Europe/Zurich")).date()
    return day, day + timedelta(days=30)


def renewal_periods(accepted_at, now):
    """Anchor every year to admission, including return to 29 February."""
    day = accepted_at.astimezone(ZoneInfo("Europe/Zurich")).date()
    today = now.astimezone(ZoneInfo("Europe/Zurich")).date()
    periods = (
        date(year, day.month, min(day.day, monthrange(year, day.month)[1]))
        for year in range(day.year + 1, today.year + 1)
    )
    return [
        (period, period + timedelta(days=30)) for period in periods if period <= today
    ]


def can_remind(*, now, due_on, paid_at):
    return paid_at is None and now.astimezone(ZoneInfo("Europe/Zurich")).date() > due_on


def can_terminate(*, now, reminder_sent_at, paid_at):
    return (
        paid_at is None
        and reminder_sent_at is not None
        and now >= reminder_sent_at + timedelta(days=60)
    )
