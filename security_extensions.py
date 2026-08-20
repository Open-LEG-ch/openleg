# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared Flask security extensions used across blueprints."""

try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address

    limiter = Limiter(
        get_remote_address,
        default_limits=["500 per hour"],
        strategy="fixed-window",
    )
except ImportError:
    limiter = None


def rate_limit(rule: str):
    """Decorate a route with a limit when Flask-Limiter is installed."""
    if limiter is not None:
        return limiter.limit(rule)
    return lambda view: view
