# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared Flask security extensions used across blueprints."""

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(
    get_remote_address,
    default_limits=["500 per hour"],
    strategy="fixed-window",
)


def rate_limit(rule: str):
    """Decorate a route with a required Flask-Limiter rule."""
    return limiter.limit(rule)
