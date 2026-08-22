# SPDX-License-Identifier: AGPL-3.0-or-later
"""The scheduled-job surface.

No product request ever reaches these routes. They run behind ``CRON_SECRET``
and fail closed: an unset secret denies every call, so a misconfigured
deployment cannot silently expose them. The operator surface moved out of
``app.py`` the same way in #271.
"""

import logging

from flask import Blueprint, abort, current_app, jsonify, request

import billing_runner
import database as db
import email_automation
import leg_registry
from admin import require_admin
from security_utils import log_security_event

logger = logging.getLogger(__name__)

cron_bp = Blueprint("cron", __name__)


def _require_cron_secret():
    """Cron endpoints fail closed: no CRON_SECRET configured means no access."""
    secret = request.headers.get("X-Cron-Secret") or request.args.get("secret") or ""
    configured = current_app.config["CRON_SECRET"]
    if not configured or secret != configured:
        log_security_event("CRON_ACCESS_DENIED", "Invalid cron secret", "WARNING")
        abort(403)


@cron_bp.route("/api/cron/process-emails", methods=["POST"])
def api_cron_process_emails():
    _require_cron_secret()
    result = email_automation.process_email_queue(app=current_app)
    return jsonify(result)


@cron_bp.route("/api/cron/refresh-public-data", methods=["POST"])
def api_cron_refresh_public_data():
    _require_cron_secret()
    import public_data

    result = public_data.refresh_canton("ZH")
    return jsonify(result)


@cron_bp.route("/api/cron/backfill-elcom", methods=["POST"])
def api_cron_backfill_elcom():
    _require_cron_secret()
    import public_data

    year = request.args.get("year", 2026, type=int)
    limit = request.args.get("limit", 25, type=int) or 25
    safe_limit = max(1, min(limit, 200))
    bfs_numbers = db.get_profile_bfs_missing_elcom_tariffs(year=year, limit=safe_limit)

    result = {
        "year": year,
        "limit": safe_limit,
        "candidates": len(bfs_numbers),
        "processed": 0,
        "saved": 0,
        "errors": [],
    }
    for bfs in bfs_numbers:
        result["processed"] += 1
        try:
            tariffs = public_data.fetch_elcom_tariffs(bfs, year=year)
            if tariffs:
                result["saved"] += int(db.save_elcom_tariffs(tariffs) or 0)
        except Exception:
            logger.exception("ElCom backfill failed for BFS %s", bfs)
            result["errors"].append({"bfs": bfs, "error": "fetch_failed"})
    return jsonify(result)


@cron_bp.route("/api/email/stats")
def api_email_stats():
    require_admin()
    return jsonify(db.get_email_stats())


@cron_bp.route("/api/cron/process-billing", methods=["POST"])
def api_cron_process_billing():
    _require_cron_secret()

    communities = db.get_active_communities()
    period_start, period_end = billing_runner.previous_complete_month()
    processed = 0
    already_processed = 0
    failures = []
    for community in communities:
        community_id = community["community_id"]
        try:
            result = billing_runner.run_billing_period(
                community_id, period_start, period_end
            )
        except billing_runner.BillingRunError:
            logger.error("Billing run failed for community %s", community_id)
            failures.append(
                {"community_id": community_id, "error": "billing_run_failed"}
            )
            continue
        if result["status"] == "created":
            processed += 1
        elif result["status"] == "already_processed":
            already_processed += 1
    return jsonify(
        {
            "activated": True,
            "status": "ok" if not failures else "partial_failure",
            "processed": processed,
            "already_processed": already_processed,
            "failed": len(failures),
            "failures": failures,
            "communities": len(communities),
        }
    )


@cron_bp.route("/api/cron/verify-registry-entries", methods=["POST"])
def api_cron_verify_registry_entries():
    _require_cron_secret()

    result = leg_registry.send_verification_nudges(
        base_url=current_app.config["SITE_URL"]
    )
    return jsonify(result)
