# SPDX-License-Identifier: AGPL-3.0-or-later
import csv
import hmac
import io
import json
import os
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import (
    Blueprint,
    Response,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
)

import agentmail_event
import billing_workspace
import database as db
import leg_registry
import sdat_ingestion
import vnb_calculated_values
from security_utils import log_security_event

try:
    from svix.webhooks import Webhook, WebhookVerificationError

    HAS_SVIX = True
except ImportError:
    HAS_SVIX = False

admin_bp = Blueprint("admin", __name__)


def require_admin():
    admin_token = os.getenv("ADMIN_TOKEN", "").strip()
    if not admin_token:
        abort(404)
    token = (
        request.headers.get("X-Admin-Token")
        or request.args.get("token")
        or request.form.get("token")
        or ""
    )
    if not hmac.compare_digest(token.encode("utf-8"), admin_token.encode("utf-8")):
        log_security_event("ADMIN_ACCESS_DENIED", "Invalid admin token", "WARNING")
        abort(403)


def require_internal_token():
    token = request.headers.get("X-Internal-Token") or ""
    internal_token = os.getenv("INTERNAL_TOKEN", "").strip()
    if not internal_token or not hmac.compare_digest(
        token.encode("utf-8"), internal_token.encode("utf-8")
    ):
        abort(403)


def _latest_snapshot_by_category(snapshots):
    latest = {}
    for snapshot in snapshots:
        latest.setdefault(snapshot.get("category", "uncategorized"), snapshot)
    return latest


_TERRITORY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


@admin_bp.route("/admin/sdat-schedules")
def admin_sdat_schedules():
    require_admin()
    return jsonify({"schedules": db.list_sdat_ingestion_schedules()})


@admin_bp.route("/admin/vnb-calculated-values/<territory>", methods=["GET", "POST"])
def admin_vnb_calculated_values(territory):
    """Import or inspect calculated values without exposing source payloads."""
    require_admin()
    if not _TERRITORY_RE.fullmatch(territory):
        return jsonify({"error": "invalid_territory"}), 400
    if request.method == "GET":
        return jsonify({"deliveries": db.list_calculated_values_deliveries(territory)})
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "invalid_payload"}), 400
    delivery = payload.get("delivery")
    if not isinstance(delivery, dict):
        return jsonify({"error": "invalid_delivery"}), 400
    # The path is the authenticated tenant scope; clients cannot select another.
    delivery["territory"] = territory
    transport = payload.get("transport", "manual")
    result = vnb_calculated_values.accept_delivery(
        delivery,
        transport=transport,
        evidence=request.get_data(cache=True),
    )
    return jsonify(result), 201 if result["status"] == "accepted" else 422


@admin_bp.route("/admin/sdat-schedules/<territory>/run", methods=["POST"])
def admin_run_sdat_schedule(territory):
    """Run one tenant now, including same-day recovery after a failure."""
    require_admin()
    schedules = db.list_sdat_ingestion_schedules()
    schedule = next(
        (item for item in schedules if item.get("territory") == territory), None
    )
    if schedule is None:
        abort(404)
    # Use the stored territory for any filesystem work: the request value
    # only selects the row, the row is the trusted source of the path.
    return jsonify(sdat_ingestion.run(schedule["territory"], schedule))


@admin_bp.route("/admin/sdat-schedules/<territory>", methods=["PUT"])
def admin_update_sdat_schedule(territory):
    require_admin()
    if not _TERRITORY_RE.fullmatch(territory):
        return jsonify({"error": "invalid_territory"}), 400
    data = request.get_json(silent=True) or {}
    timezone_name = data.get("timezone", "Europe/Zurich")
    try:
        ZoneInfo(timezone_name)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        return jsonify({"error": "invalid_timezone"}), 400
    local_time = data.get("local_time", "02:00")
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(local_time)):
        return jsonify({"error": "invalid_local_time"}), 400
    try:
        max_attempts = int(data.get("max_attempts", 3))
        retry_seconds = int(data.get("retry_seconds", 30))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_retry"}), 400
    if not 1 <= max_attempts <= 5 or not 0 <= retry_seconds <= 300:
        return jsonify({"error": "invalid_retry"}), 400
    local_dir = data.get("local_dir")
    try:
        # Validate without touching the filesystem: the request values are
        # stored first and resolved from the stored schedule at run time.
        sdat_ingestion.validate_tenant_directory(territory, local_dir)
    except sdat_ingestion.IngestionError:
        return jsonify({"error": "invalid_local_dir"}), 400
    schedule = db.upsert_sdat_ingestion_schedule(
        territory,
        {
            "enabled": data.get("enabled") is True,
            "timezone": timezone_name,
            "local_time": local_time,
            "local_dir": local_dir,
            "max_attempts": max_attempts,
            "retry_seconds": retry_seconds,
        },
    )
    local_time_value = schedule.get("local_time")
    if hasattr(local_time_value, "strftime"):
        schedule["local_time"] = local_time_value.strftime("%H:%M")
    return jsonify(schedule)


def _verify_agentmail_request():
    webhook_secret = os.getenv("AGENTMAIL_WEBHOOK_SECRET", "").strip()
    if webhook_secret:
        if not HAS_SVIX:
            abort(503)
        try:
            Webhook(webhook_secret).verify(
                request.get_data(),
                {
                    "svix-id": request.headers.get("svix-id", ""),
                    "svix-timestamp": request.headers.get("svix-timestamp", ""),
                    "svix-signature": request.headers.get("svix-signature", ""),
                },
            )
            return
        except WebhookVerificationError:
            abort(403)
    require_internal_token()


@admin_bp.route("/admin/overview")
def admin_overview():
    require_admin()
    stats = db.get_stats()
    email_stats = db.get_email_stats()
    consented = db.count_consented_buildings()
    municipalities = db.get_all_municipalities()
    return jsonify(
        {
            "platform": "OpenLEG",
            "stats": stats,
            "email_stats": email_stats,
            "consented_buildings": consented,
            "municipalities": len(municipalities),
            "db_available": db.is_db_available(),
        }
    )


@admin_bp.route("/admin/export")
def admin_export():
    require_admin()
    fmt = (request.args.get("format") or "json").lower()
    city_id = request.args.get("city_id")

    buildings = db.get_operator_building_profiles(city_id=city_id)
    if fmt == "csv":
        output = io.StringIO()
        if buildings:
            writer = csv.DictWriter(output, fieldnames=buildings[0].keys())
            writer.writeheader()
            for row in buildings:
                writer.writerow(row)
        response = Response(output.getvalue(), mimetype="text/csv")
        response.headers["Content-Disposition"] = (
            "attachment; filename=openleg_export.csv"
        )
        return response
    return jsonify({"records": buildings, "count": len(buildings)})


@admin_bp.route("/api/internal/lea-report", methods=["POST"])
def api_internal_lea_report():
    require_internal_token()
    data = request.get_json(silent=True) or {}
    job_name = data.get("job_name", "unknown")
    summary = data.get("summary", "")
    status = data.get("status", "ok")
    db.save_lea_report(job_name, summary, status)
    return jsonify({"ok": True})


@admin_bp.route("/admin/lea-reports")
def admin_lea_reports():
    require_admin()
    reports = db.get_lea_reports(limit=50)
    return jsonify({"reports": reports})


@admin_bp.route("/api/internal/ops-snapshot", methods=["POST"])
def api_internal_ops_snapshot():
    require_internal_token()
    data = request.get_json(silent=True) or {}
    db.save_ops_snapshot(
        source=data.get("source", "unknown"),
        category=data.get("category", "general"),
        summary_text=data.get("summary", ""),
        status=data.get("status", "ok"),
        payload=data.get("payload") if isinstance(data.get("payload"), dict) else {},
    )
    return jsonify({"ok": True})


@admin_bp.route("/api/internal/agentmail", methods=["POST"])
def api_internal_agentmail():
    _verify_agentmail_request()
    event = agentmail_event.build_event(request.get_json(silent=True))
    if event["event_type"] not in {
        "message.received",
        "message.received.unauthenticated",
        "inbound_email.received",
    }:
        return jsonify({"ok": True, "ignored": True})
    summary = event.get("subject") or event.get("from_email") or "Inbound LEA mail"
    db.save_ops_snapshot(
        source="agentmail",
        category="lea_inbox",
        summary_text=summary,
        status="received",
        payload=event,
    )
    return jsonify({"ok": True})


@admin_bp.route("/admin/ops")
def admin_ops():
    require_admin()
    snapshots = db.get_ops_snapshots(limit=100)
    reports = db.get_lea_reports(limit=20)
    latest = _latest_snapshot_by_category(snapshots)
    pending_registry = leg_registry.annotate_vnb_plausibility(
        db.list_registry_entries(moderation_status="pending")
    )
    stale_registry = db.get_registry_entries_needing_verification(
        stale_days=leg_registry.VERIFICATION_STALE_DAYS, limit=1000
    )
    sdat_schedules = db.list_sdat_ingestion_schedules()
    interest_records = db.get_operator_interest_records(limit=500)
    response = {
        "latest": latest,
        "snapshots": snapshots[:20],
        "reports": reports,
        "pending_registry": pending_registry,
        "sdat_schedules": sdat_schedules,
        "interest_records": interest_records,
        "counts": {
            "lea_inbox": sum(1 for s in snapshots if s.get("category") == "lea_inbox"),
            "github_monitor": sum(
                1 for s in snapshots if s.get("category") == "github_monitor"
            ),
            "vnb_monitor": sum(
                1 for s in snapshots if s.get("category") == "vnb_monitor"
            ),
            "stuck_formations": sum(
                1 for s in snapshots if s.get("category") == "stuck_formations"
            ),
            "registry_pending": db.get_registry_pending_count(),
            "registry_stale": len(stale_registry),
            **db.get_operator_interest_counts(),
        },
    }
    if "text/html" in (request.headers.get("Accept") or ""):
        return render_template("admin/ops.html", **response)
    return Response(
        json.dumps(response, default=str),
        mimetype="application/json",
    )


@admin_bp.route("/admin/abrechnungen", methods=["GET", "POST"])
def admin_billing_workspace():
    """Render the read-only audit surface for billing drafts."""
    require_admin()
    raw_period_id = request.values.get("period_id")
    try:
        period_id = int(raw_period_id) if raw_period_id is not None else None
    except (TypeError, ValueError):
        abort(404)
    try:
        workspace = billing_workspace.load(period_id=period_id)
    except billing_workspace.BillingPeriodNotFound:
        abort(404)
    except db.BillingStoreError:
        abort(503)
    return render_template(
        "admin/abrechnungen.html",
        **workspace,
        contact_email=os.getenv("ADMIN_EMAIL", "hallo@openleg.ch"),
    )


@admin_bp.route("/admin/registry/<int:entry_id>/approve", methods=["POST"])
def admin_registry_approve(entry_id):
    require_admin()
    db.update_registry_entry_moderation(entry_id, "published", "")
    admin_token = os.getenv("ADMIN_TOKEN", "").strip()
    return redirect(f"/admin/ops?token={admin_token}")


@admin_bp.route("/admin/registry/<int:entry_id>/reject", methods=["POST"])
def admin_registry_reject(entry_id):
    require_admin()
    reason = (request.form.get("reason") or "").strip()
    db.update_registry_entry_moderation(entry_id, "rejected", reason)
    admin_token = os.getenv("ADMIN_TOKEN", "").strip()
    return redirect(f"/admin/ops?token={admin_token}")


@admin_bp.route("/api/email/stats")
def api_email_stats():
    require_admin()
    return jsonify(db.get_email_stats())
