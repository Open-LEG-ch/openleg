# SPDX-License-Identifier: AGPL-3.0-or-later
"""Mark domain display text for extraction without changing stored values."""


def gettext_noop(message):
    """Return the original text; translate only at the presentation boundary."""
    return message
