# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pre-registered public OAuth clients, authorization code and S256 PKCE."""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import uuid
from urllib.parse import urlencode, urlsplit

from flask import (
    Blueprint,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    session,
)

import database as db
import formation_wizard
from i18n import gettext as _
from operator_api import CAPABILITIES, _admin_for, _require_csrf, _token_hash
from translation_markers import gettext_noop as _N

oauth_bp = Blueprint("oauth", __name__)


@oauth_bp.get("/.well-known/oauth-authorization-server")
def metadata():
    issuer = current_app.config["OAUTH_ISSUER"]
    return jsonify(
        issuer=issuer,
        authorization_endpoint=issuer + "/oauth/authorize",
        token_endpoint=issuer + "/oauth/token",
        response_types_supported=["code"],
        grant_types_supported=["authorization_code"],
        code_challenge_methods_supported=["S256"],
        token_endpoint_auth_methods_supported=["none"],
    )


@oauth_bp.after_request
def private_headers(response):
    response.headers.update(
        {
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "Referrer-Policy": "no-referrer",
        }
    )
    return response


def downstream_token(access_token):
    # Domain separation keeps the downstream bearer distinct from the random MCP token.
    return (
        "olk_"
        + hashlib.sha256(("oauth-downstream:" + access_token).encode()).hexdigest()
    )


def invalid(error="invalid_request", status=400):
    return jsonify(error=error), status


def scopes(value):
    words = value.split()
    communities = [s[10:] for s in words if s.startswith("community:")]
    caps = [s for s in words if not s.startswith("community:")]
    if (
        len(communities) > 1
        or (communities and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", communities[0]))
        or not caps
        or len(words) != len(set(words))
        or not set(caps) <= CAPABILITIES
    ):
        raise ValueError
    return communities[0] if communities else None, caps


@oauth_bp.route("/oauth/authorize", methods=["GET", "POST"])
def authorize():
    p = request.args if request.method == "GET" else request.form
    if any(len(p.getlist(k)) != 1 for k in p if k != "capabilities"):
        return invalid()
    if p.get("resource") != current_app.config["OAUTH_RESOURCE"]:
        return invalid("invalid_target")
    client = current_app.config.get("OAUTH_CLIENTS", {}).get(p.get("client_id"))
    if not client or p.get("redirect_uri") not in client["redirect_uris"]:
        return invalid()
    if (
        p.get("response_type") != "code"
        or p.get("code_challenge_method") != "S256"
        or not re.fullmatch(r"[A-Za-z0-9_-]{43}", p.get("code_challenge", ""))
    ):
        return invalid()
    try:
        community, caps = scopes(p.get("scope", ""))
    except ValueError:
        return invalid("invalid_scope")
    building = session.get("dashboard_building_id")
    if not building:
        return invalid("access_denied", 403)
    communities = [
        row
        for row in formation_wizard.get_user_communities(building)
        if _admin_for(row["community_id"], building)
    ]
    chosen = p.get("community_id", community)
    if not communities or (
        chosen and chosen not in {r["community_id"] for r in communities}
    ):
        return invalid("access_denied", 403)
    if request.method == "POST":
        community = chosen
        if not community:
            return invalid("invalid_scope")
        selected = p.getlist("capabilities") if p.get("capabilities_present") else caps
        if not selected or not set(selected) <= set(caps):
            return invalid("invalid_scope")
        caps = list(dict.fromkeys(selected))
    if request.method == "GET":
        session.setdefault("dashboard_csrf_token", secrets.token_urlsafe(32))
        return render_template(
            "oauth_consent.html",
            client=client["name"],
            community=chosen,
            communities=communities,
            capabilities=[(c, _(LABELS[c])) for c in caps],
            fields=p,
            csrf=session["dashboard_csrf_token"],
            _=_,
            private_page=True,
        )
    if not _require_csrf():
        return invalid("access_denied", 403)
    query = {"state": p["state"]} if "state" in p else {}
    if p.get("decision") != "approve":
        query["error"] = "access_denied"
    else:
        code = "olc_" + secrets.token_urlsafe(32)
        payload = {
            k: p[k]
            for k in (
                "client_id",
                "redirect_uri",
                "code_challenge",
                "resource",
                "scope",
            )
        }
        payload["scope"] = " ".join(["community:" + community, *caps])
        payload.update(
            community_id=community,
            capabilities=caps,
            building_id=building,
            name=client["name"],
            issuer=current_app.config["OAUTH_ISSUER"],
        )
        db.save_oauth_code(_token_hash(code), payload)
        query["code"] = code
    uri = p["redirect_uri"]
    return redirect(uri + ("&" if "?" in uri else "?") + urlencode(query))


@oauth_bp.post("/oauth/token")
def token():
    p = request.form
    if any(len(p.getlist(k)) != 1 for k in p):
        return invalid()
    if p.get("grant_type") != "authorization_code":
        return invalid("unsupported_grant_type")
    if p.get("resource") != current_app.config["OAUTH_RESOURCE"]:
        return invalid("invalid_target")
    client = current_app.config.get("OAUTH_CLIENTS", {}).get(p.get("client_id"))
    if (
        not client
        or p.get("redirect_uri") not in client["redirect_uris"]
        or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", p.get("code_verifier", ""))
    ):
        return invalid("invalid_grant")
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(p["code_verifier"].encode()).digest())
        .decode()
        .rstrip("=")
    )
    grant_id = str(uuid.uuid4())
    access = "ola_" + secrets.token_urlsafe(32)
    downstream = downstream_token(access)
    grant = db.redeem_oauth_code(
        _token_hash(p.get("code", "")),
        p["client_id"],
        p["redirect_uri"],
        challenge,
        p["resource"],
        grant_id,
        _token_hash(access),
        _token_hash(downstream),
    )
    if not grant:
        return invalid("invalid_grant")
    return jsonify(
        access_token=access, token_type="Bearer", expires_in=300, scope=grant["scope"]
    )


@oauth_bp.post("/oauth/introspect")
def introspect():
    secret = current_app.config.get("OAUTH_INTROSPECTION_SECRET", "")
    if len(secret) < 32 or not hmac.compare_digest(
        request.headers.get("Authorization", ""), "Bearer " + secret
    ):
        return invalid("invalid_client", 401)
    row = db.introspect_oauth_token(_token_hash(request.form.get("token", "")))
    if not row:
        return jsonify(active=False)
    downstream = downstream_token(request.form.get("token", ""))
    if not hmac.compare_digest(_token_hash(downstream), row["downstream_hash"]):
        return jsonify(active=False)
    p = row["payload"]
    return jsonify(
        active=True,
        iss=p["issuer"],
        aud=p["resource"],
        exp=row["exp"],
        scope=p["scope"],
        community_id=p["community_id"],
        downstream_token=downstream,
    )


LABELS = {
    "formation.read": _N("Gründungsanmeldungen lesen"),
    "formation.mutate": _N("Gründungsanmeldungen einreichen"),
    "membership.read": _N("Mitgliedermutationen lesen"),
    "membership.mutate": _N("Mitgliedermutationen einreichen"),
    "metering.read": _N("Messdatenläufe lesen"),
    "metering.mutate": _N("Messdatenläufe wiederholen"),
    "billing.read": _N("Abrechnungen lesen"),
    "cases.read": _N("Rechnungsanfragen lesen"),
    "cases.mutate": _N("Rechnungsanfragen beantworten"),
    "payments.read": _N("Zahlungsabgleiche lesen"),
    "payments.mutate": _N("Zahlungsabgleiche bestätigen"),
}


def init_oauth(app):
    """OAuth is opt-in; fail startup on incomplete or unsafe configuration."""
    issuer = app.config.get("OAUTH_ISSUER") or os.environ.get("OPENLEG_OAUTH_ISSUER")
    if not issuer:
        return
    app.config.setdefault("OAUTH_ISSUER", issuer)
    for key in ("RESOURCE", "INTROSPECTION_SECRET"):
        app.config.setdefault(
            "OAUTH_" + key, os.environ.get("OPENLEG_OAUTH_" + key, "")
        )
    app.config.setdefault(
        "OAUTH_CLIENTS", json.loads(os.environ.get("OPENLEG_OAUTH_CLIENTS", "{}"))
    )
    for key in ("OAUTH_ISSUER", "OAUTH_RESOURCE"):
        u = urlsplit(app.config[key])
        if (
            not u.hostname
            or u.username
            or u.password
            or u.query
            or u.fragment
            or (
                u.scheme != "https"
                and not (
                    u.scheme == "http"
                    and u.hostname in {"localhost", "127.0.0.1", "::1"}
                )
            )
        ):
            raise ValueError("OAuth URLs require HTTPS or loopback HTTP")
    if urlsplit(issuer).path:
        raise ValueError("OAuth issuer must be an origin without a trailing slash")
    for key in ("OAUTH_INTROSPECTION_SECRET",):
        if len(app.config[key]) < 32:
            raise ValueError("OAuth secrets require at least 32 random characters")
    for client in app.config["OAUTH_CLIENTS"].values():
        if not isinstance(client["name"], str) or not 1 <= len(client["name"]) <= 200:
            raise ValueError("OAuth client name is required")
        for uri in client["redirect_uris"]:
            u = urlsplit(uri)
            if (
                u.fragment
                or u.username
                or u.password
                or not u.hostname
                or (
                    u.scheme != "https"
                    and not (
                        u.scheme == "http"
                        and u.hostname in {"127.0.0.1", "localhost", "::1"}
                    )
                )
            ):
                raise ValueError("Unsafe OAuth redirect URI")
    app.register_blueprint(oauth_bp)
