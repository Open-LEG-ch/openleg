# SPDX-License-Identifier: AGPL-3.0-or-later
"""Canonical locale policy shared by storage and notification delivery."""

import os

from flask import current_app, has_app_context

_ALIASES = {
    "de": "de_CH",
    "de-CH": "de_CH",
    "de_CH": "de_CH",
    "fr": "fr_CH",
    "fr-CH": "fr_CH",
    "fr_CH": "fr_CH",
}

_RELEASE_ALIASES = {
    "en": "en",
    "en-CH": "en",
    "en_CH": "en",
    "it": "it_CH",
    "it-CH": "it_CH",
    "it_CH": "it_CH",
}


def extended_locales_enabled():
    if has_app_context():
        return current_app.config.get("SITE_RELEASE_ENABLED") is True
    return os.environ.get("OPENLEG_SITE_RELEASE_ENABLED", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def normalize_locale(value, *, extended=None):
    """Return the canonical identifier for a supported alias, or None."""
    if not isinstance(value, str):
        return None
    selected = _ALIASES.get(value)
    if selected is not None:
        return selected
    if extended is None:
        extended = extended_locales_enabled()
    return _RELEASE_ALIASES.get(value) if extended else None


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
