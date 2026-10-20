# SPDX-License-Identifier: AGPL-3.0-or-later
"""Explicit deployment validation mode shared by HTTP and database access."""

import os

# Endpoint names bind this list to reviewed read-only handlers, including the
# public routes used by release validation. Unknown and private routes fail shut.
_PUBLIC_ENDPOINTS = frozenset(
    {
        "static",
        "health.health",
        "health.livez",
        "main.index",
        "main.login",
        "main.how_it_works",
        "main.fuer_bewohner",
        "main.fuer_gemeinden",
        "main.open_source",
        "main.leg_gruenden",
        "main.leg_kalkulator",
        "main.pricing",
        "main.impressum",
        "main.datenschutz",
        "main.robots_txt",
        "main.llms_txt",
        "main.sitemap_xml",
        "main.favicon",
        "municipality.profil",
        "rangliste.hub",
        "rangliste.methodik",
        "self_host.self_host_page",
    }
)


def read_only_enabled():
    """Fail startup on unknown values instead of silently enabling writes."""
    value = os.environ.get("OPENLEG_RELEASE_READ_ONLY", "")
    if value in {"", "0"}:
        return False
    if value == "1":
        return True
    raise RuntimeError("OPENLEG_RELEASE_READ_ONLY must be 0 or 1")


def install_http_guard(app, *, read_only):
    """Install before locale, tenant, or extension hooks can reach handlers."""
    app.config["OPENLEG_RELEASE_READ_ONLY"] = read_only
    if not read_only:
        return

    from flask import Response, request

    @app.before_request
    def guard_release_request():
        if request.method in {"GET", "HEAD"} and request.endpoint in _PUBLIC_ENDPOINTS:
            return None
        response = Response(
            "Service temporarily unavailable.\n", status=503, mimetype="text/plain"
        )
        response.headers["Retry-After"] = "60"
        response.headers["Cache-Control"] = "no-store"
        return response
