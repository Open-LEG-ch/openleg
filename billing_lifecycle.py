# SPDX-License-Identifier: AGPL-3.0-or-later
"""State rules and display vocabulary for immutable invoice lifecycles."""

from datetime import date

from translation_markers import gettext_noop as _N


class InvoiceLifecycleError(ValueError):
    """A requested invoice state transition is invalid or incomplete."""


STATES = ("issued", "delivered", "paid", "cancelled", "corrected")
STATE_LABELS = {
    "issued": _N("Freigegeben"),
    "delivered": _N("Zugestellt"),
    "paid": _N("Bezahlt"),
    "cancelled": _N("Storniert"),
    "corrected": _N("Korrigiert"),
}

_TRANSITIONS = {
    ("issued", "deliver"): "delivered",
    ("delivered", "pay"): "paid",
    ("issued", "cancel"): "cancelled",
    ("delivered", "cancel"): "cancelled",
    ("cancelled", "correct"): "corrected",
}


def _required_text(value, message):
    if not isinstance(value, str) or not value.strip():
        raise InvoiceLifecycleError(message)
    return value.strip()


def next_state(
    current_state, event, *, reason=None, reference=None, effective_date=None
):
    """Validate one state change and return its resulting canonical state."""
    new_state = _TRANSITIONS.get((current_state, event))
    if new_state is None:
        raise InvoiceLifecycleError(_N("Dieser Statuswechsel ist nicht zulässig."))
    if event in {"cancel", "correct"}:
        _required_text(reason, _N("Ein Grund ist erforderlich."))
    if event == "pay":
        _required_text(reference, _N("Eine Zahlungsreferenz ist erforderlich."))
        if not isinstance(effective_date, date):
            raise InvoiceLifecycleError(
                _N("Ein gültiges Zahlungsdatum ist erforderlich.")
            )
    return new_state


def describe_invoice(invoice):
    """Add member/admin display labels to a lifecycle-aware invoice row."""
    described = dict(invoice)
    state = invoice.get("lifecycle_state") or "issued"
    if state not in STATES:
        raise InvoiceLifecycleError(_N("Die Rechnung hat einen ungültigen Status."))
    described["lifecycle_state"] = state
    described["status_label"] = STATE_LABELS[state]
    return described
