# SPDX-License-Identifier: AGPL-3.0-or-later
"""Private-response HTTP policy.

One module decides which responses are private and stamps both headers
(`Cache-Control: no-store`, `Referrer-Policy: no-referrer`). The application
calls `apply_private_response_headers` once per response after the security
middleware, so route handlers never mark responses one by one.
"""

from flask import request, session

PRIVATE_RESPONSE_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
}

# Token-bearing and member-only surfaces are private with or without a session.
_PRIVATE_PATH_PREFIXES = (
    "/dashboard/access/",
    "/dashboard/invoices",
    "/gemeinde/access/",
    "/registry/verify/",
    "/leg/document/",
)

# Resident and LEG operator surfaces turn private once a dashboard session exists.
_DASHBOARD_SESSION_PATHS = frozenset(
    {
        "/dashboard",
        "/dashboard/export",
        "/dashboard/profile",
        "/leg/dashboard",
    }
)
_DASHBOARD_SESSION_PREFIXES = ("/leg/community/",)

_MUNICIPALITY_SESSION_PATHS = frozenset({"/gemeinde/dashboard"})


def is_private_response(
    path, *, dashboard_session: bool, municipality_session: bool
) -> bool:
    """Classify one request path; the session flags mark authenticated viewers."""
    if path.startswith(_PRIVATE_PATH_PREFIXES):
        return True
    if dashboard_session and (
        path in _DASHBOARD_SESSION_PATHS or path.startswith(_DASHBOARD_SESSION_PREFIXES)
    ):
        return True
    return municipality_session and path in _MUNICIPALITY_SESSION_PATHS


def apply_private_response_headers(response):
    """Stamp both private headers when the current request classified private."""
    dashboard_building_id = session.get("dashboard_building_id")
    if is_private_response(
        request.path,
        dashboard_session=(
            isinstance(dashboard_building_id, str)
            and bool(dashboard_building_id.strip())
        ),
        municipality_session=bool(session.get("municipality_id")),
    ):
        for header, value in PRIVATE_RESPONSE_HEADERS.items():
            response.headers[header] = value
    return response
