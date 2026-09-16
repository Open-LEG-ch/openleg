# SPDX-License-Identifier: AGPL-3.0-or-later
"""Domain rules for invoice-scoped member questions."""

import os
from datetime import datetime, timedelta, timezone

CATEGORIES = frozenset({"amount", "metering", "payment", "other"})
STATUSES = frozenset({"open", "acknowledged", "resolved"})
TRANSITIONS = {
    "open": frozenset({"acknowledged", "resolved"}),
    "acknowledged": frozenset({"resolved"}),
    "resolved": frozenset(),
}


def deadlines(opened_at: datetime | None = None) -> tuple[datetime, datetime]:
    """Return response and reminder deadlines from bounded operator settings."""
    response_days = int(os.getenv("INVOICE_QUERY_RESPONSE_DAYS", "10"))
    reminder_days = int(os.getenv("INVOICE_QUERY_REMINDER_DAYS", "2"))
    if response_days < 1 or reminder_days < 1 or reminder_days >= response_days:
        raise ValueError("Ungültige Fristen für Rechnungsfragen.")
    opened_at = opened_at or datetime.now(timezone.utc)
    return (
        opened_at + timedelta(days=response_days),
        opened_at + timedelta(days=response_days - reminder_days),
    )


def validate_open(category: str, message: str) -> tuple[str, str]:
    category = (category or "").strip()
    message = (message or "").strip()
    if category not in CATEGORIES:
        raise ValueError("Bitte wählen Sie einen gültigen Grund.")
    if not message or len(message) > 4000:
        raise ValueError("Die Nachricht muss zwischen 1 und 4000 Zeichen enthalten.")
    return category, message


def validate_message(message: str) -> str:
    message = (message or "").strip()
    if not message or len(message) > 4000:
        raise ValueError("Die Nachricht muss zwischen 1 und 4000 Zeichen enthalten.")
    return message


def require_transition(current: str, target: str) -> None:
    if target not in STATUSES or target not in TRANSITIONS.get(current, frozenset()):
        raise ValueError("Dieser Statuswechsel ist nicht erlaubt.")
