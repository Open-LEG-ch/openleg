# SPDX-License-Identifier: AGPL-3.0-or-later
"""Membership contribution policy, independent of persistence and HTTP."""

from datetime import timedelta
from zoneinfo import ZoneInfo

ANNUAL_DUES_CHF = 50


def admission_dates(accepted_at):
    day = accepted_at.astimezone(ZoneInfo("Europe/Zurich")).date()
    return day, day + timedelta(days=30)


def can_remind(*, now, due_on, paid_at):
    return paid_at is None and now.astimezone(ZoneInfo("Europe/Zurich")).date() > due_on


def can_terminate(*, now, reminder_sent_at, paid_at):
    return (
        paid_at is None
        and reminder_sent_at is not None
        and now >= reminder_sent_at + timedelta(days=60)
    )
