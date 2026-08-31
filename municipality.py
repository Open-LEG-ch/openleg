# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Municipality onboarding for OpenLEG platform.
Handles Gemeinde signup, admin dashboard, LEG formation KPIs.
"""

import hmac
import logging
import secrets

from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    session,
)

import access_token
import database as db
import email_utils
import municipality_profile
import pv_data
import security_utils
from cantons import SWISS_CANTONS
from ranking import Ranking
from security_extensions import rate_limit

logger = logging.getLogger(__name__)

municipality_bp = Blueprint("municipality", __name__, url_prefix="/gemeinde")
pilot_bp = Blueprint("pilot", __name__, url_prefix="/pilotgemeinde")

PILOT_MUNICIPALITIES = municipality_profile.PILOT_MUNICIPALITIES


@municipality_bp.route("/onboarding")
def onboarding():
    return render_template(
        "gemeinde/onboarding.html",
        site_url=request.url_root.rstrip("/"),
        canonical_path="/gemeinde/onboarding",
    )


@municipality_bp.route("/register", methods=["POST"])
def register():
    data = request.json or {}
    bfs = data.get("bfs_number")
    admin_email = data.get("admin_email", "").strip()

    if not bfs or not admin_email:
        return jsonify({"error": "BFS-Nummer und E-Mail erforderlich."}), 400

    is_valid, normalized, error = security_utils.validate_email_address(admin_email)
    if not is_valid:
        return jsonify({"error": error}), 400

    profile = db.get_municipality_profile(int(bfs))
    if not profile:
        return jsonify({"error": "Unbekannte BFS-Nummer."}), 400

    name = (profile.get("name") or "").strip()
    kanton = (profile.get("kanton") or "").strip().upper()[:2]
    population = profile.get("population")
    subdomain = (
        name.lower()
        .replace(" ", "-")
        .replace("ü", "ue")
        .replace("ä", "ae")
        .replace("ö", "oe")
    )

    muni_id = db.save_municipality(
        bfs_number=int(bfs),
        name=name,
        kanton=kanton or "ZH",
        dso_name=None,
        population=population,
        subdomain=subdomain,
    )

    if muni_id:
        db.update_municipality_status(int(bfs), "registered", admin_email=normalized)
        db.track_event("municipality_registered", data={"bfs": bfs, "name": name})
        return jsonify(
            {"success": True, "municipality_id": muni_id, "subdomain": subdomain}
        )

    return jsonify({"error": "Registrierung fehlgeschlagen."}), 500


ONBOARDING_STATUS_LABELS = {
    "pending": "In Prüfung",
    "active": "Aktiv",
    "verified": "Verifiziert",
}


def _dashboard_context(muni):
    subdomain = (muni.get("subdomain") or "").strip()
    stats = db.get_stats(city_id=subdomain or None) or {}
    profile = None
    if muni.get("bfs_number"):
        try:
            profile = db.get_municipality_profile(int(muni["bfs_number"]))
        except Exception:
            logger.warning(
                "municipality profile load failed for bfs=%s",
                muni.get("bfs_number"),
                exc_info=True,
            )
            profile = None
    profile = profile or {}
    if subdomain:
        invite_url = f"https://{subdomain}.openleg.ch"
    else:
        invite_url = current_app.config["APP_BASE_URL"].rstrip("/")
    return {
        "municipality": muni,
        "status_label": ONBOARDING_STATUS_LABELS.get(
            muni.get("onboarding_status"), "In Prüfung"
        ),
        "stats": stats,
        "solar_score": profile.get("pv_score_pct"),
        "energy_score": profile.get("energy_transition_score"),
        "invite_url": invite_url,
        "error": None,
    }


@municipality_bp.route("/dashboard")
def dashboard():
    municipality_id = session.get("municipality_id")
    if not municipality_id:
        return render_template(
            "gemeinde/dashboard.html",
            municipality=None,
            error=(
                "Der Zugangslink ist ungültig oder bereits verwendet."
                if request.args.get("access") == "invalid"
                else None
            ),
            access_required=True,
        )

    muni = db.get_municipality(municipality_id=municipality_id)

    if not muni:
        return render_template(
            "gemeinde/dashboard.html",
            municipality=None,
            error="Gemeinde nicht gefunden.",
        )

    return render_template("gemeinde/dashboard.html", **_dashboard_context(muni))


@municipality_bp.route("/access/request", methods=["POST"])
@rate_limit("5 per minute")
def access_request():
    email = (request.form.get("email") or "").strip().lower()
    generic_message = (
        "Falls eine Gemeinde zu dieser E-Mail-Adresse existiert, haben wir einen "
        "neuen Zugangslink gesendet."
    )
    is_valid, normalized_email, _error = security_utils.validate_email_address(email)
    municipality = (
        db.get_municipality_by_admin_email(normalized_email)
        if is_valid and normalized_email
        else None
    )
    if municipality:
        municipality_id = municipality.get("id")
        token = access_token.issue(
            access_token.MUNICIPALITY, db, municipality_id, ttl_seconds=900
        )
        if token:
            url = access_token.access_url(
                access_token.MUNICIPALITY, current_app.config["APP_BASE_URL"], token
            )
            try:
                email_utils.send_email(
                    normalized_email,
                    "Ihr Gemeinde-Dashboard-Zugangslink",
                    "Öffnen Sie Ihr Gemeinde-Dashboard über diesen Link:\n\n"
                    f"{url}\n\n"
                    "Falls Sie diesen Link nicht angefordert haben, können Sie "
                    "diese E-Mail ignorieren.",
                )
            except Exception:
                current_app.logger.exception(
                    "Failed to send municipality dashboard access email"
                )
    return render_template(
        "gemeinde/dashboard.html",
        municipality=None,
        error=None,
        access_required=True,
        access_request_message=generic_message,
    )


@municipality_bp.route("/access/<token>")
def access_exchange(token):
    municipality_id = access_token.consume(access_token.MUNICIPALITY, db, token)
    if not municipality_id:
        response = redirect("/gemeinde/dashboard?access=invalid")
    else:
        session.clear()
        session.permanent = True
        session["municipality_id"] = municipality_id
        session["municipality_csrf_token"] = secrets.token_urlsafe(32)
        response = redirect("/gemeinde/dashboard")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@municipality_bp.route("/logout", methods=["POST"])
def logout():
    municipality_id = session.get("municipality_id")
    submitted = request.form.get("csrf_token", "")
    expected = session.get("municipality_csrf_token", "")
    if not municipality_id:
        abort(401)
    if (
        not isinstance(submitted, str)
        or not submitted.isascii()
        or not expected
        or not hmac.compare_digest(submitted, expected)
    ):
        abort(400)
    db.revoke_municipality_access_tokens(municipality_id)
    session.clear()
    return redirect("/")


@municipality_bp.route("/dashboard/demo")
def dashboard_demo():
    """Fake, click-through municipality dashboard for demos and screenshots."""
    return render_template(
        "gemeinde/dashboard.html",
        municipality={
            "name": "Baden",
            "bfs_number": 4021,
            "subdomain": "baden",
            "dso_name": "Regionalwerke AG Baden",
            "onboarding_status": "active",
        },
        status_label="Aktiv",
        stats={"total_buildings": 42, "registrations_today": 3},
        solar_score=34,
        energy_score=61,
        invite_url="https://baden.openleg.ch",
        error=None,
    )


@municipality_bp.route("/api/municipalities")
def api_municipalities():
    profiles = db.get_all_municipality_profiles()
    return jsonify(
        {
            "municipalities": [
                {
                    "bfs": p.get("bfs_number"),
                    "name": p.get("name", ""),
                    "population": p.get("population"),
                    "score": float(p.get("energy_transition_score", 0) or 0),
                    "kanton": p.get("kanton", ""),
                }
                for p in profiles
            ]
        }
    )


@municipality_bp.route("/profil/<int:bfs>")
def profil(bfs):
    ctx = municipality_profile.profile_context(
        bfs, site_url=request.url_root.rstrip("/")
    )
    if ctx is None:
        abort(404)
    return render_template("gemeinde/profil.html", **ctx)


@pilot_bp.route("/<slug>")
def pilot_case_study(slug):
    ctx = municipality_profile.pilot_context(
        slug, site_url=request.url_root.rstrip("/")
    )
    if ctx is None:
        abort(404)
    return render_template("gemeinde/pilotgemeinde.html", **ctx)


def _normalize_kanton_param(raw_value):
    raw = (raw_value or "all").strip().upper()
    if raw in ("", "ALL"):
        return None, "all"
    if raw in SWISS_CANTONS:
        return raw, raw
    return None, "all"


@municipality_bp.route("/verzeichnis")
def verzeichnis():
    kanton_filter, kanton = _normalize_kanton_param(request.args.get("kanton"))
    order_by = request.args.get("sort", "energy_transition_score")
    query = request.args.get("q", "").strip()
    profiles = db.get_all_municipality_profiles(kanton=kanton_filter, order_by=order_by)
    if order_by in {
        "energy_transition_score",
        "leg_value_gap_chf",
        "population",
        "pv_score_pct",
    }:
        profiles = list(reversed(profiles))
    if query:
        profiles = [
            profile
            for profile in profiles
            if query.lower() in (profile.get("name", "") or "").lower()
        ]
    ranking_rows = {row["bfs_number"]: row for row in Ranking.load().national()}
    for profile in profiles:
        row = ranking_rows.get(profile.get("bfs_number"), {})
        profile["pv_rank"] = row.get("rank")
        profile["display_score"] = row.get("display_score")
        profile["score_over_100"] = row.get("score_over_100")
    return render_template(
        "gemeinde/verzeichnis.html",
        profiles=profiles,
        kanton=kanton,
        query=query,
        sort=order_by,
        site_url=request.url_root.rstrip("/"),
        canton_options=[
            ("all", "Alle Kantone"),
            *((code, code) for code in sorted(SWISS_CANTONS)),
        ],
        canonical_path="/gemeinde/verzeichnis",
        data_vintage=pv_data.SNAPSHOT_YEAR,
        plant_match_rate=pv_data.PLANT_MATCH_RATE_PCT,
    )
