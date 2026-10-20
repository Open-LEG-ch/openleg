# SPDX-License-Identifier: AGPL-3.0-or-later
"""Canonical locale policy shared by storage and notification delivery."""

_ALIASES = {
    "de": "de_CH",
    "de-CH": "de_CH",
    "de_CH": "de_CH",
    "fr": "fr_CH",
    "fr-CH": "fr_CH",
    "fr_CH": "fr_CH",
}


def normalize_locale(value):
    """Return the canonical identifier for a supported alias, or None."""
    return _ALIASES.get(value) if isinstance(value, str) else None


def require_recipient_locale(value):
    """Refuse unsupported persisted choices instead of silently changing them."""
    selected = normalize_locale(value)
    if selected is None:
        raise ValueError("unsupported_recipient_locale")
    return selected


def resolve_locale(*candidates):
    """First supported choice wins; untrusted/unknown values cannot select files."""
    return next(
        (
            locale
            for value in (*candidates, "de_CH")
            if (locale := normalize_locale(value))
        ),
        "de_CH",
    )
