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
    "/confirm/",
    "/interest/confirm/",
    "/unsubscribe/",
    "/admin/vnb-calculated-values",
    "/api/operator/",
    "/api/telemetry/",
    "/api/equipment/",
    "/api/admin/advisories/",
    "/dashboard/access/",
    "/dashboard/invoices",
    "/dashboard/contacts",
    "/dashboard/equipment",
    "/dashboard/telemetry",
    "/gemeinde/access/",
    "/registry/verify/",
    "/leg/document/",
    "/leg/community/",
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
    if path.startswith(_PRIVATE_PATH_PREFIXES) or (
        path.startswith("/leg/community/") and "/archive" in path
    ):
        return True
    if dashboard_session and (
        path in _DASHBOARD_SESSION_PATHS or path.startswith(_DASHBOARD_SESSION_PREFIXES)
    ):
        return True
    return municipality_session and path in _MUNICIPALITY_SESSION_PATHS


def request_is_private():
    """One classification shared by headers and template resource decisions."""
    building_id = session.get("dashboard_building_id")
    sensitive_parameters = {"token", "access_token", "verification_token", "secret"}
    return (
        bool(sensitive_parameters.intersection(request.args))
        or bool(
            session.get("utility_client_id") and request.path.startswith("/utility/")
        )
        or is_private_response(
            request.path,
            dashboard_session=isinstance(building_id, str)
            and bool(building_id.strip()),
            municipality_session=bool(session.get("municipality_id")),
        )
    )


def template_context():
    return {"private_page": request_is_private()}


def apply_private_response_headers(response):
    """Stamp both private headers when the current request classified private."""
    if request_is_private():
        for header, value in PRIVATE_RESPONSE_HEADERS.items():
            response.headers[header] = value
        # Inline product scripts/styles remain usable, but no third-party URL
        # can receive this page's token, even if a future template adds one.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "font-src 'self' data:; connect-src 'self'; form-action 'self'; "
            "frame-src 'none'; object-src 'none'; base-uri 'self'"
        )
    return response
