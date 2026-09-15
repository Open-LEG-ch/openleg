# SPDX-License-Identifier: AGPL-3.0-or-later
"""Domain rules for invoice-scoped member questions."""

CATEGORIES = frozenset({"amount", "metering", "payment", "other"})
STATUSES = frozenset({"open", "acknowledged", "resolved"})
TRANSITIONS = {
    "open": frozenset({"acknowledged", "resolved"}),
    "acknowledged": frozenset({"resolved"}),
    "resolved": frozenset(),
}


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
