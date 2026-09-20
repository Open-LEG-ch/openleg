# SPDX-License-Identifier: AGPL-3.0-or-later
"""Dashboard readiness verb."""

import json
import math
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from urllib.parse import quote, urlencode

import battery_asset
import billing_lifecycle
import billing_policy
import billing_workspace
import community_access
import community_archive
import database as db
import formation_documents
import formation_wizard
import member_invoices
import member_savings
import payment_reconciliation
import security_utils
import vnb_exchange

_PROFILE_EXPORT_FIELDS = (
    "building_id",
    "email",
    "phone",
    "address",
    "lat",
    "lon",
    "plz",
    "building_type",
    "annual_consumption_kwh",
    "potential_pv_kwp",
    "registered_at",
    "verified",
    "verified_at",
    "user_type",
    "city_id",
    "share_with_neighbors",
    "share_with_utility",
    "updates_opt_in",
    "consent_version",
)


def leg_dashboard_location(community_id: str) -> str:
    """Build the dashboard redirect target with untrusted values encoded.

    urlencode keeps the location a relative /leg/dashboard path no matter
    what the caller passes (no protocol-relative //host, no fragments).
    """
    return "/leg/dashboard?" + urlencode({"cid": community_id})


def readiness(building_id: str, *, city_id=None, app_base_url: str = "") -> dict:
    """Compute readiness view for one building.

    Returns a dict with user, readiness_score, checks, neighbor_count,
    referral_link and error. On missing / unknown building_id, user is None
    and error is set.
    """
    if not building_id:
        return {"error": "Kein Profil angegeben.", "user": None}

    user = db.get_building_for_dashboard(building_id)
    if not user:
        return {"error": "Profil nicht gefunden.", "user": None}

    score = 0
    checks = []
    if user.get("verified"):
        score += 25
        checks.append(("E-Mail bestätigt", True))
    else:
        checks.append(("E-Mail bestätigt", False))
    if user.get("annual_consumption_kwh"):
        score += 25
        checks.append(("Verbrauchsdaten hinterlegt", True))
    else:
        checks.append(("Verbrauchsdaten hinterlegt", False))
    if user.get("share_with_utility"):
        score += 25
        checks.append(("EVU-Einwilligung erteilt", True))
    else:
        checks.append(("EVU-Einwilligung erteilt", False))
    if user.get("share_with_neighbors"):
        score += 25
        checks.append(("Nachbar-Einwilligung erteilt", True))
    else:
        checks.append(("Nachbar-Einwilligung erteilt", False))

    neighbor_count = 0
    lat = user.get("lat")
    lon = user.get("lon")
    if lat is not None and lon is not None:
        neighbor_count = db.get_neighbor_count_near(
            float(lat), float(lon), city_id=city_id
        )

    referral_link = ""
    ref_code = db.get_referral_code(building_id)
    if ref_code:
        referral_link = f"{app_base_url}/?ref={ref_code}"

    return {
        "user": user,
        "readiness_score": score,
        "checks": checks,
        "neighbor_count": neighbor_count,
        "neighbor_box_half_width_m": int(db.NEIGHBOR_BOX_HALF_WIDTH_KM * 1000),
        "referral_link": referral_link,
        "error": None,
    }


def _with_german_labels(community: dict) -> dict:
    """Return a shallow copy with German display labels for rendering."""
    labeled = dict(community)
    labeled["status_label"] = formation_wizard.FORMATION_STATUS_LABELS.get(
        labeled.get("status"), "Status wird geprüft"
    )
    labeled["distribution_model_label"] = (
        formation_wizard.DISTRIBUTION_MODEL_LABELS.get(
            labeled.get("distribution_model"),
            "Verteilmodell wird geprüft",
        )
    )
    labeled_members = []
    for member in labeled.get("members") or []:
        labeled_member = dict(member)
        labeled_member["role_label"] = formation_wizard.MEMBER_ROLE_LABELS.get(
            labeled_member.get("role"), "Rolle wird geprüft"
        )
        labeled_member["status_label"] = formation_wizard.MEMBER_STATUS_LABELS.get(
            labeled_member.get("status"), "Status wird geprüft"
        )
        labeled_members.append(labeled_member)
    labeled["members"] = labeled_members
    return labeled


def leg_overview(community_id: str, building_id: str) -> dict:
    """Operator view of one community, gated on membership.

    Same capability-URL model as the resident dashboard: the caller must
    present a building_id that is a member of the community. Non-members
    get the error view, never another community's data.
    """
    if not community_id or not building_id:
        return {"error": "Kein Zugriff.", "community": None}

    status = formation_wizard.get_community_status(community_id)
    if not status:
        return {"error": "LEG nicht gefunden.", "community": None}

    member = next(
        (m for m in status["members"] or [] if m["building_id"] == building_id),
        None,
    )
    if not member:
        return {"error": "Kein Zugriff.", "community": None}

    vnb_exchange_available = db.is_db_available()
    if vnb_exchange_available:
        try:
            vnb_submissions = db.list_vnb_submission_cases(community_id)
            vnb_mutations = db.list_vnb_mutations(community_id)
        except db.VnbExchangeStoreError:
            vnb_exchange_available = False
            vnb_submissions = []
            vnb_mutations = []
    else:
        vnb_submissions = []
        vnb_mutations = []
    capabilities = community_access.capabilities_for(member)
    return {
        "error": None,
        "community": _with_german_labels(status),
        "minimum_community_size": formation_wizard.FORMATION_CONFIG[
            "min_community_size"
        ],
        "viewer_building_id": building_id,
        "is_admin": community_access.is_administrator(member),
        "can_manage_members": community_access.MANAGE_MEMBERS in capabilities,
        "can_manage_documents": community_access.MANAGE_DOCUMENTS in capabilities,
        "can_prepare_billing": community_access.PREPARE_BILLING in capabilities,
        "can_approve_billing": community_access.APPROVE_BILLING in capabilities,
        "can_audit_billing": community_access.AUDIT_BILLING in capabilities,
        "access_role_labels": community_access.ROLE_LABELS,
        "leg_documents": db.list_leg_documents(community_id),
        "correspondence": db.list_correspondence(community_id),
        "battery": _battery_overview(community_id),
        "battery_member_ids": (
            confirmed_member_ids(community_id)
            if community_access.is_administrator(member)
            or community_access.PREPARE_BILLING in capabilities
            else []
        ),
        "vnb_submissions": vnb_submissions,
        "vnb_mutations": vnb_mutations,
        "vnb_exchange_available": vnb_exchange_available,
    }


def _require_role(community_id: str, building_id: str, role: str):
    """Return the member row if building_id has the given role, else None."""
    status = formation_wizard.get_community_status(community_id)
    if not status:
        return None
    return next(
        (
            m
            for m in status["members"] or []
            if m["building_id"] == building_id and m.get("role") == role
        ),
        None,
    )


def _require_confirmed_admin(community_id: str, building_id: str):
    """Return the member row for a confirmed community admin, else None."""
    member = _require_role(community_id, building_id, "admin")
    if not member or member.get("status") != "confirmed":
        return None
    return member


def leg_export_archive(community_id: str, building_id: str) -> bytes | None:
    """Export one LEG only for its confirmed administrator."""
    if not _require_confirmed_admin(community_id, building_id):
        return None
    return community_archive.export_community_archive(community_id)


def leg_restore_archive(
    community_id: str, building_id: str, archive: bytes, *, dry_run: bool
) -> dict | None:
    """Validate or restore one LEG archive under confirmed-admin control."""
    if not _require_confirmed_admin(community_id, building_id):
        return None
    try:
        payload = json.loads(archive)
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = {}
    manifest = payload.get("manifest", {}) if isinstance(payload, dict) else {}
    if not isinstance(manifest, dict):
        manifest = {}
    if manifest.get("community_id") not in {None, community_id}:
        return {
            "valid": False,
            "errors": ["Archive belongs to another community"],
            "conflicts": [],
        }
    return community_archive.restore_community_archive(archive, dry_run=dry_run)


def _require_capability(community_id: str, building_id: str, capability: str):
    """Return a confirmed member when the central access policy permits it."""
    status = formation_wizard.get_community_status(community_id)
    if not status:
        return None
    member = next(
        (m for m in status["members"] or [] if m["building_id"] == building_id),
        None,
    )
    return member if community_access.allows(member, capability) else None


def leg_billing_workspace_location(community_id: str) -> str:
    """Build the billing workspace path; quote keeps it a relative path."""
    return "/leg/community/" + quote(community_id, safe="") + "/billing"


_BILLING_STATUS_LABELS = {"draft": "Entwurf", "issued": "Freigegeben"}
InvoiceLifecycleError = billing_lifecycle.InvoiceLifecycleError


def _veracity_flag_counts(periods) -> dict:
    """Auffällige Messfenster pro Periode, gebündelt in einer Ledger-Lesung.

    Ein Flag sperrt nichts: die Zahl macht Befunde sichtbar, die der
    Operator vor der Freigabe sehen soll (#517).
    """
    if not periods:
        return {}
    starts = [
        period.get("period_start") for period in periods if period.get("period_start")
    ]
    ends = [period.get("period_end") for period in periods if period.get("period_end")]
    if not starts or not ends:
        return {}
    flags = db.get_sdat_veracity_flags(min(starts), max(ends))
    counts: dict = {}
    for flag in flags:
        window_start = flag.get("window_start")
        for period in periods:
            period_start = period.get("period_start")
            period_end = period.get("period_end")
            if (
                period_start
                and period_end
                and window_start
                and period_start <= window_start <= period_end
            ):
                counts[period["id"]] = counts.get(period["id"], 0) + 1
    return counts


def _billing_workspace_period(period: dict, veracity_flag_count: int = 0) -> dict:
    """Compact display row for one billing period in the workspace."""
    status = str(period.get("status") or "")
    flags = billing_workspace.readiness_flags(period)
    return {
        "id": period.get("id"),
        "status": status,
        "status_label": _BILLING_STATUS_LABELS.get(status, status),
        "period_label": billing_workspace.period_label(period.get("period_start")),
        "reconciled": flags["reconciled"],
        "source_count": flags["source_count"],
        "veracity_flag_count": veracity_flag_count,
        "prepared_by": period.get("prepared_by"),
        "approvable": (
            status == "draft" and flags["reconciled"] and flags["source_count"] > 0
        ),
    }


def _display_gross_chf(invoice: dict) -> tuple[str, bool]:
    """Show the stored gross, or an explicit unreadable marker; never 0.00.

    The operator approves and records payments against this list, so a row
    whose totals are missing, null, or malformed is marked "Unlesbar" and
    carries no invented amount. This mirrors the member display layer's
    fail-closed honesty without failing the whole workspace closed.
    """
    try:
        amount = Decimal(str(invoice.get("gross_chf")))
    except (InvalidOperation, ValueError):
        return "Unlesbar", True
    if not amount.is_finite():
        return "Unlesbar", True
    return f"{amount:.2f}", False


def leg_billing_workspace_view(
    community_id: str,
    building_id: str,
    *,
    include_statement_entries: bool = False,
    **extra,
) -> dict:
    """Capability-gated view model for the billing approval workspace."""
    member = _require_capability(
        community_id, building_id, community_access.AUDIT_BILLING
    )
    if not member:
        return {"error": "Kein Zugriff."}
    capabilities = community_access.capabilities_for(member)
    periods = db.list_community_billing_periods(community_id)
    flag_counts = _veracity_flag_counts(periods)
    invoices = [
        billing_lifecycle.describe_invoice(invoice)
        for invoice in db.list_community_invoices(community_id)
    ]
    events_by_invoice = {}
    community_events = (
        db.list_community_invoice_events(community_id) if invoices else []
    )
    for event in community_events:
        events_by_invoice.setdefault(event["invoice_id"], []).append(event)
    queries_by_invoice = {}
    if db.is_db_available():
        invoice_queries = db.list_invoice_queries(None, community_id=community_id)
    else:
        # Unit and demo configurations can intentionally run without a DB pool.
        invoice_queries = []
    for query in invoice_queries:
        queries_by_invoice.setdefault(query["invoice_id"], []).append(query)
    for invoice in invoices:
        (
            invoice["display_gross_chf"],
            invoice["gross_unreadable"],
        ) = _display_gross_chf(invoice)
        policy_snapshot = invoice.get("policy_snapshot")
        delivery_method = (
            policy_snapshot.get("delivery_method")
            if isinstance(policy_snapshot, dict)
            else None
        )
        invoice["delivery_method_label"] = billing_policy.DELIVERY_METHOD_LABELS.get(
            delivery_method, "Nicht angegeben"
        )
        invoice["display_unit_price_rp"] = billing_policy.rate_rp_text(
            policy_snapshot.get("internal_price_chf_per_kwh")
            if isinstance(policy_snapshot, dict)
            else None
        )
        invoice["events"] = [
            {
                **event,
                "previous_status_label": billing_lifecycle.STATE_LABELS.get(
                    event.get("previous_state"), event.get("previous_state")
                ),
                "new_status_label": billing_lifecycle.STATE_LABELS.get(
                    event.get("new_state"), event.get("new_state")
                ),
            }
            for event in events_by_invoice.get(invoice["id"], [])
        ]
        invoice["correction_candidates"] = [
            {
                "id": candidate["id"],
                "invoice_number": candidate["invoice_number"],
            }
            for candidate in invoices
            if candidate["id"] != invoice["id"]
            and candidate.get("participant_id") == invoice.get("participant_id")
            and candidate["lifecycle_state"] == "issued"
            and not candidate.get("corrects_invoice_number")
        ]
        invoice["queries"] = queries_by_invoice.get(invoice["id"], [])
    view = {
        "error": None,
        "community_id": community_id,
        "periods": [
            _billing_workspace_period(period, flag_counts.get(period.get("id"), 0))
            for period in periods
        ],
        "invoices": invoices,
        "statement_entries": [
            {
                **entry,
                "match_decision_label": payment_reconciliation.DECISION_LABELS.get(
                    entry.get("match_decision"),
                    entry.get("match_decision") or "Unbekannt",
                ),
            }
            for entry in (
                db.list_bank_statement_entries(community_id)
                if include_statement_entries
                else []
            )
        ],
        "billing_approved": False,
        "approval_error": None,
        "viewer_building_id": building_id,
        "require_dual_control": bool(
            (formation_wizard.get_community_status(community_id) or {}).get(
                "require_dual_control"
            )
        ),
        "can_prepare_billing": community_access.PREPARE_BILLING in capabilities,
        "can_approve_billing": community_access.APPROVE_BILLING in capabilities,
    }
    view.update(extra)
    return view


def leg_import_bank_statement(community_id, building_id, source_name, content):
    """Import a statement only for a confirmed administrator of the LEG."""
    if not _require_confirmed_admin(community_id, building_id):
        return {"error": "Kein Zugriff."}
    result = payment_reconciliation.import_statement(
        community_id, building_id, source_name, content, db
    )
    return {"error": None, **result}


def leg_deliver_invoice(
    community_id, building_id, invoice_id, *, send_email, invoice_url
):
    """Reserve and complete one idempotent portal or email delivery."""
    if not _require_capability(
        community_id, building_id, community_access.PREPARE_BILLING
    ):
        return {"error": "Kein Zugriff."}
    delivery = db.prepare_invoice_delivery(invoice_id, community_id, building_id)
    if delivery.get("already_delivered"):
        return {"error": None, **delivery}
    if delivery.get("confirmation_required"):
        return {"error": None, **delivery}
    if delivery["delivery_method"] == "email":
        recipient = delivery.get("recipient_email")
        sent = bool(
            recipient
            and send_email(
                recipient,
                f"Ihre Rechnung {delivery['invoice_number']}",
                (
                    "Ihre neue LEG-Rechnung ist im geschützten OpenLEG-Dashboard "
                    f"verfügbar: {invoice_url}"
                ),
            )
        )
        if not sent:
            error = "E-Mail-Versand fehlgeschlagen"
            db.fail_invoice_delivery(invoice_id, community_id, building_id, error)
            return {"error": "Die Rechnung konnte nicht per E-Mail zugestellt werden."}
    completed = db.complete_invoice_delivery(invoice_id, community_id, building_id)
    return {"error": None, **completed}


def leg_confirm_invoice_delivery(community_id, building_id, invoice_id):
    """Let an admin resolve an uncertain send without repeating the email."""
    if not _require_capability(
        community_id, building_id, community_access.PREPARE_BILLING
    ):
        return {"error": "Kein Zugriff."}
    confirmed = db.confirm_invoice_delivery(invoice_id, community_id, building_id)
    return {"error": None, **confirmed}


def leg_record_invoice_payment(
    community_id, building_id, invoice_id, paid_date, reference
):
    if not _require_capability(
        community_id, building_id, community_access.PREPARE_BILLING
    ):
        return {"error": "Kein Zugriff."}
    try:
        parsed_date = date.fromisoformat(paid_date)
    except (TypeError, ValueError):
        raise InvoiceLifecycleError(
            "Ein gültiges Zahlungsdatum ist erforderlich."
        ) from None
    db.record_invoice_payment(
        invoice_id, community_id, building_id, parsed_date, reference
    )
    return {"error": None}


def leg_cancel_invoice(community_id, building_id, invoice_id, reason):
    if not _require_capability(
        community_id, building_id, community_access.PREPARE_BILLING
    ):
        return {"error": "Kein Zugriff."}
    db.cancel_invoice(invoice_id, community_id, building_id, reason)
    return {"error": None}


def leg_correct_invoice(
    community_id, building_id, invoice_id, corrected_invoice_id, reason
):
    if not _require_capability(
        community_id, building_id, community_access.PREPARE_BILLING
    ):
        return {"error": "Kein Zugriff."}
    try:
        corrected_id = int(corrected_invoice_id)
    except (TypeError, ValueError):
        raise InvoiceLifecycleError(
            "Eine gültige Ersatzrechnung ist erforderlich."
        ) from None
    db.correct_invoice(invoice_id, corrected_id, community_id, building_id, reason)
    return {"error": None}


def leg_approve_billing_period(
    community_id: str, building_id: str, period_id: int
) -> dict:
    """Issue invoices for one reconciled draft; only the confirmed admin may."""
    if not _require_capability(
        community_id, building_id, community_access.APPROVE_BILLING
    ):
        return {"error": "Kein Zugriff.", "invoices": []}
    status = formation_wizard.get_community_status(community_id) or {}
    if status.get("require_dual_control"):
        period = db.get_billing_period(period_id) or {}
        if not period.get("prepared_by") or period.get("prepared_by") == building_id:
            return {
                "error": (
                    "Vorbereitung und Freigabe müssen durch verschiedene "
                    "Personen erfolgen."
                ),
                "invoices": [],
            }
    invoices = db.approve_billing_period(
        period_id, community_id, approver_id=building_id
    )
    return {"error": None, "invoices": invoices}


def leg_prepare_billing_period(
    community_id: str, building_id: str, period_id: int
) -> dict:
    """Record the confirmed human who submits a draft for approval."""
    if not _require_capability(
        community_id, building_id, community_access.PREPARE_BILLING
    ):
        return {"error": "Kein Zugriff.", "error_status": 403}
    try:
        recorded = db.record_billing_period_preparer(
            period_id, community_id, building_id
        )
    except db.BillingStoreError:
        return {
            "error": "Abrechnung vorübergehend nicht verfügbar.",
            "error_status": 503,
        }
    if not recorded:
        return {
            "error": "Abrechnungsperiode nicht gefunden oder nicht mehr im Entwurf.",
            "error_status": 409,
        }
    return {"error": None}


def leg_billing_policy_location(community_id: str) -> str:
    """Build the billing policy path; quote keeps it a relative path."""
    return "/leg/community/" + quote(community_id, safe="") + "/billing-policy"


def leg_billing_policy_view(community_id: str, building_id: str, **extra) -> dict:
    """Admin-gated view model for the versioned billing policy page."""
    if not _require_capability(
        community_id, building_id, community_access.PREPARE_BILLING
    ):
        return {"error": "Kein Zugriff."}
    versions = db.list_billing_policies(community_id)
    view = {
        "error": None,
        "community_id": community_id,
        "policy_versions": [
            billing_policy.describe_version(version) for version in versions
        ],
        "policy_disclaimer": billing_policy.POLICY_DISCLAIMER,
        "policy_labels": {
            "network_level": billing_policy.NETWORK_LEVEL_LABELS,
            "distribution_model": billing_policy.DISTRIBUTION_MODEL_LABELS,
            "vat_mode": billing_policy.VAT_MODE_LABELS,
            "delivery_method": billing_policy.DELIVERY_METHOD_LABELS,
        },
        "form_errors": {},
        "form_values": {},
        "policy_saved": False,
    }
    view.update(extra)
    return view


def leg_save_billing_policy(community_id: str, building_id: str, form) -> dict:
    """Validate and persist one new policy version; only the admin may write."""
    if not _require_capability(
        community_id, building_id, community_access.PREPARE_BILLING
    ):
        return {"error": "Kein Zugriff.", "errors": {}}
    result = billing_policy.validate_policy_form(form)
    if result["errors"]:
        return {"error": None, "errors": result["errors"]}
    try:
        db.save_billing_policy(community_id, result["policy"])
    except db.BillingPolicyConflict:
        return {
            "error": None,
            "errors": {
                "effective_from": (
                    "Für dieses Gültig-ab-Datum existiert bereits eine Version."
                )
            },
        }
    return {"error": None, "errors": {}}


def _battery_overview(community_id: str) -> dict:
    """Display model of the community's shared storage asset, fail-soft."""
    try:
        asset = db.get_battery_asset(community_id)
    except db.BillingStoreError:
        return {"configured": False, "asset": None, "store_error": True}
    if not asset:
        return {"configured": False, "asset": None, "store_error": False}
    capacity = asset["capacity_kwh"]
    annual_cost = asset["annual_cost_chf"]
    shares = []
    for participant_id, share_pct in asset["shares"]:
        amount = (share_pct * annual_cost / Decimal(100)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        shares.append(
            {
                "participant_id": participant_id,
                "share_pct_display": format(share_pct, "f"),
                "share_amount_display": format(amount, "f"),
            }
        )
    return {
        "configured": True,
        "asset": {
            "name": asset["name"],
            "capacity_kwh_display": format(capacity, "f"),
            "annual_cost_chf_display": format(annual_cost, "f"),
            "shares": shares,
        },
        "store_error": False,
    }


def confirmed_member_ids(community_id: str) -> list[str]:
    """The confirmed members whose building ids key the cost-share inputs."""
    status = formation_wizard.get_community_status(community_id)
    if not status:
        return []
    return sorted(
        member["building_id"]
        for member in status["members"] or []
        if member.get("status") == "confirmed"
    )


def leg_save_battery_asset(community_id: str, building_id: str, form) -> dict:
    """Validate and persist the community's storage asset and cost shares.

    Only billing preparation may write. The share inputs are keyed by
    confirmed member building_id; a community without confirmed members
    cannot configure a battery.
    """
    if not _require_capability(
        community_id, building_id, community_access.PREPARE_BILLING
    ):
        return {"error": "Kein Zugriff.", "errors": {}}
    participants = confirmed_member_ids(community_id)
    result = battery_asset.validate_battery_form(form, participants)
    if result["errors"]:
        return {"error": None, "errors": result["errors"]}
    try:
        db.save_battery_asset(community_id, result["asset"])
    except db.BillingStoreError:
        return {
            "error": None,
            "errors": {
                "shares": (
                    "Der Quartierakku konnte nicht gespeichert werden. "
                    "Bitte erneut versuchen."
                )
            },
        }
    return {"error": None, "errors": {}}


MemberInvoiceDataError = member_invoices.MemberInvoiceDataError


def member_invoices_view(building_id: str) -> dict:
    """Own issued invoices only, newest first. Thin seam over member_invoices."""
    return member_invoices.list_view(building_id)


def member_invoice_detail(invoice_id: int, building_id: str) -> dict | None:
    """One own issued invoice, or None for a missing or another member's id."""
    invoice = member_invoices.detail_view(invoice_id, building_id)
    if invoice:
        invoice["queries"] = db.list_invoice_queries(
            invoice_id, participant_id=building_id
        )
    return invoice


def member_open_invoice_query(
    invoice_id: int,
    building_id: str,
    category: str,
    message: str,
    attachment_filename: str = "",
    attachment_data: bytes | None = None,
) -> dict:
    """Open a question against the caller's own immutable invoice."""
    try:
        query_id = db.open_invoice_query(
            invoice_id,
            building_id,
            category,
            message,
            attachment_filename,
            attachment_data,
        )
    except ValueError as exc:
        return {"error": str(exc), "query_id": None}
    if not query_id:
        return {"error": "Rechnung nicht gefunden.", "query_id": None}
    return {"error": None, "query_id": query_id}


def member_reply_invoice_query(query_id: int, building_id: str, message: str) -> dict:
    """Append a member reply only inside that member's case scope."""
    try:
        changed = db.add_invoice_query_message(
            query_id, building_id, message, participant_id=building_id
        )
    except ValueError as exc:
        return {"error": str(exc)}
    return {"error": None if changed else "Kein Zugriff."}


def operator_update_invoice_query(
    community_id: str,
    building_id: str,
    query_id: int,
    *,
    message: str = "",
    status: str = "",
) -> dict:
    """Reply to or transition a case through billing-preparer authority."""
    if not _require_capability(
        community_id, building_id, community_access.PREPARE_BILLING
    ):
        return {"error": "Kein Zugriff."}
    if not (message or "").strip() and not (status or "").strip():
        return {"error": "Antwort oder Status ist erforderlich."}
    try:
        if not db.update_invoice_query(
            query_id,
            community_id,
            building_id,
            message=message,
            target_status=status,
        ):
            return {"error": "Kein Zugriff."}
    except ValueError as exc:
        return {"error": str(exc)}
    return {"error": None}


def member_invoice_pdf_bytes(invoice: dict) -> bytes:
    """Render the exact detail view dict as a printable PDF."""
    return member_invoices.render_pdf(invoice)


MemberSavingsDataError = member_savings.MemberSavingsDataError


def member_invoice_savings_view(invoice_id: int, building_id: str) -> dict | None:
    """Realized savings behind one own invoice, from metered E66 readings.

    Thin seam over member_savings. Returns None for a missing or another
    member's invoice id; raises MemberSavingsDataError when the stored
    billing or metering data cannot be shown honestly.
    """
    return member_savings.invoice_savings_view(invoice_id, building_id)


def leg_create(name: str, building_id: str, distribution_model: str) -> dict:
    """Create a community with building_id as admin."""
    name = (name or "").strip()
    if not name or not building_id:
        return {"error": "Name und Profil sind erforderlich.", "community_id": None}
    if distribution_model not in ("simple", "proportional", "custom"):
        distribution_model = "simple"
    created = formation_wizard.create_community(name, building_id, distribution_model)
    if not created:
        return {
            "error": "LEG konnte nicht erstellt werden.",
            "community_id": None,
        }
    return {"error": None, "community_id": created["community_id"]}


def leg_invite(community_id: str, building_id: str, invite_building_id: str) -> dict:
    """Invite a building; only the community admin may invite."""
    if not _require_capability(
        community_id, building_id, community_access.MANAGE_MEMBERS
    ):
        return {"error": "Nur die Administration kann einladen."}
    if not invite_building_id:
        return {"error": "Kein Profil zum Einladen angegeben."}
    ok = formation_wizard.invite_member(community_id, invite_building_id, building_id)
    if not ok:
        return {"error": "Einladung nicht möglich (bereits Mitglied?)."}
    return {"error": None}


def leg_invite_by_email(community_id: str, building_id: str, invite_email: str) -> dict:
    """Invite a known profile by email without exposing profile identifiers.

    A valid address always gets the same response. This prevents the operator
    dashboard from becoming an email-enumeration surface.
    """
    if not _require_capability(
        community_id, building_id, community_access.MANAGE_MEMBERS
    ):
        return {"error": "Nur die Administration kann einladen."}
    valid, normalized, error = security_utils.validate_email_address(invite_email)
    if not valid or not normalized:
        return {"error": error or "Bitte geben Sie eine gültige E-Mail-Adresse ein."}

    for profile in db.get_building_by_email(normalized) or []:
        invite_building_id = (profile.get("building_id") or "").strip()
        if not invite_building_id or invite_building_id == building_id:
            continue
        formation_wizard.invite_member(community_id, invite_building_id, building_id)
        break
    return {"error": None}


def leg_set_member_roles(
    community_id: str, building_id: str, member_building_id: str, roles
) -> dict:
    """Replace one member's scoped roles through the formation repository."""
    if not _require_capability(
        community_id, building_id, community_access.MANAGE_MEMBERS
    ):
        return {"error": "Nur die Administration kann Rollen ändern."}
    try:
        changed = db.set_member_access_roles(
            community_id, member_building_id, roles, building_id
        )
    except ValueError as exc:
        return {"error": str(exc)}
    if not changed:
        return {
            "error": (
                "Die Rollen konnten nicht geändert werden. "
                "Mindestens eine Administration muss bestehen bleiben."
            )
        }
    return {"error": None}


def leg_set_dual_control(community_id: str, building_id: str, enabled: bool) -> dict:
    """Change the community billing approval policy as an administrator."""
    member = _require_capability(
        community_id, building_id, community_access.MANAGE_MEMBERS
    )
    if not community_access.is_administrator(member):
        return {"error": "Nur die Administration kann das Vier-Augen-Prinzip ändern."}
    if not db.set_community_dual_control(community_id, enabled, building_id):
        return {"error": "Das Vier-Augen-Prinzip konnte nicht geändert werden."}
    return {"error": None}


def export_profile(building_id: str) -> dict:
    """Return an explicit, JSON-safe export of one resident-owned profile."""
    profile = db.get_building(building_id) or {}
    exported = {}
    for field in _PROFILE_EXPORT_FIELDS:
        value = profile.get(field)
        if isinstance(value, float) and not math.isfinite(value):
            continue
        if value is None or isinstance(value, (str, int, float, bool)):
            exported[field] = value
        elif hasattr(value, "isoformat"):
            exported[field] = value.isoformat()
        elif field in {"lat", "lon", "annual_consumption_kwh", "potential_pv_kwp"}:
            numeric_value = float(value)
            if math.isfinite(numeric_value):
                exported[field] = numeric_value
    return exported


def leg_confirm(community_id: str, building_id: str) -> dict:
    """Confirm one's own invited membership."""
    if not community_id or not building_id:
        return {"error": "Kein Zugriff."}
    ok = formation_wizard.confirm_membership(community_id, building_id)
    if not ok:
        return {"error": "Keine offene Einladung gefunden."}
    return {"error": None}


def leg_start_formation(community_id: str, building_id: str) -> dict:
    """Start formal formation; only the community admin may start."""
    if not _require_capability(
        community_id, building_id, community_access.MANAGE_MEMBERS
    ):
        return {"error": "Nur die Administration kann die Gründung starten."}
    ok = formation_wizard.start_formation(community_id)
    if not ok:
        return {"error": "Gründung noch nicht möglich (genug bestätigte Mitglieder?)."}
    return {"error": None}


def leg_generate_documents(community_id: str, building_id: str) -> dict:
    """Generate the complete document bundle through its domain seam."""
    return formation_documents.generate(community_id, building_id)


def leg_submit_vnb_formation(community_id: str, building_id: str) -> dict:
    """Prepare or deliver the signed formation package through the VNB seam."""
    try:
        outcome = vnb_exchange.submit_formation(
            vnb_exchange.FormationSubmission(community_id, building_id),
        )
    except vnb_exchange.FormationSubmissionForbidden:
        return {
            "error": "Keine Berechtigung für die Netzbetreiber-Anmeldung.",
            "error_status": 403,
        }
    except vnb_exchange.FormationSubmissionInvalid as error:
        return {"error": str(error), "error_status": 409}
    except db.VnbSubmissionConflict as error:
        return {"error": str(error), "error_status": 409}
    except db.VnbExchangeStoreError:
        return {
            "error": "Netzbetreiber-Anmeldung vorübergehend nicht verfügbar.",
            "error_status": 503,
        }
    return {
        "error": None,
        "state": outcome.state,
        "case_id": outcome.case_id,
        "event_id": outcome.event_id,
    }


def leg_submit_vnb_mutation(
    community_id: str,
    building_id: str,
    mutation_id: str,
    participant_id: str,
    mutation_type: str,
    effective_date: str,
    source_agreement_id: str,
    after: dict,
) -> dict:
    """Submit a participant change while leaving the LEG record untouched."""
    try:
        outcome = vnb_exchange.submit_membership_mutation(
            vnb_exchange.ParticipantMutationSubmission(
                mutation_id.strip(),
                community_id,
                participant_id.strip(),
                building_id,
                mutation_type.strip(),
                effective_date.strip(),
                source_agreement_id.strip(),
                after,
            )
        )
    except vnb_exchange.FormationSubmissionForbidden:
        return {
            "error": "Keine Berechtigung für Mitgliedermutationen.",
            "error_status": 403,
        }
    except (
        vnb_exchange.ParticipantMutationInvalid,
        ValueError,
        db.VnbSubmissionConflict,
    ) as error:
        return {"error": str(error), "error_status": 409}
    except db.VnbExchangeStoreError:
        return {
            "error": "VNB-Mutation vorübergehend nicht verfügbar.",
            "error_status": 503,
        }
    return {
        "error": None,
        "state": outcome.state,
        "case_id": outcome.case_id,
        "event_id": outcome.event_id,
    }


def leg_vnb_mutation_manual_package(community_id: str, case_id: str, building_id: str):
    if not _require_capability(
        community_id, building_id, community_access.MANAGE_MEMBERS
    ):
        return None
    return db.get_vnb_mutation_manual_package(community_id, case_id)


def leg_mark_vnb_mutation_delivered(
    community_id: str, case_id: str, building_id: str
) -> dict:
    if not _require_capability(
        community_id, building_id, community_access.MANAGE_MEMBERS
    ):
        return {"error": "Keine Berechtigung.", "error_status": 403}
    try:
        row = db.mark_vnb_mutation_manual_delivered(community_id, case_id, building_id)
    except db.VnbExchangeStoreError as error:
        return {"error": str(error)}
    return {"error": None, "state": row["state"]}


def leg_vnb_manual_package(community_id: str, case_id: str, building_id: str):
    """Return a private manual handover package to an authorized operator."""
    if not _require_capability(
        community_id, building_id, community_access.MANAGE_DOCUMENTS
    ):
        return None
    return db.get_vnb_manual_package(community_id, case_id)


def leg_mark_vnb_manual_delivered(
    community_id: str, case_id: str, building_id: str
) -> dict:
    """Confirm a manual VNB handover through the domain seam."""
    try:
        outcome = vnb_exchange.mark_manual_delivered(
            vnb_exchange.ManualDeliveryConfirmation(community_id, case_id, building_id)
        )
    except (
        vnb_exchange.FormationSubmissionForbidden,
        db.VnbExchangeStoreError,
    ) as error:
        return {
            "error": str(error) or "Manuelle Zustellung fehlgeschlagen.",
            "error_status": (
                403
                if isinstance(error, vnb_exchange.FormationSubmissionForbidden)
                else 409
            ),
        }
    return {"error": None, "state": outcome.state}


def leg_document_for_member(doc_id: int, building_id: str):
    """Return a stored document only if building_id belongs to its community."""
    doc = db.get_leg_document(doc_id)
    if not doc:
        return None
    status = formation_wizard.get_community_status(doc["community_id"])
    if not status:
        return None
    is_member = any(m["building_id"] == building_id for m in status["members"] or [])
    return doc if is_member else None


def leg_log_correspondence(
    community_id: str,
    building_id: str,
    direction: str,
    channel: str,
    counterparty: str,
    subject: str,
    notes: str = "",
    attachment_filename: str = "",
    attachment_data: bytes | None = None,
) -> dict:
    """Append a ledger entry; any confirmed or invited member may log."""
    status = formation_wizard.get_community_status(community_id)
    if not status or not any(
        m["building_id"] == building_id for m in status["members"] or []
    ):
        return {"error": "Kein Zugriff."}

    if attachment_data is not None:
        if not attachment_filename.lower().endswith(".pdf"):
            return {"error": "Anhänge müssen PDF-Dateien sein."}
        if not attachment_data.startswith(b"%PDF-"):
            return {"error": "Der Anhang ist keine gültige PDF-Datei."}
        if len(attachment_data) > 2 * 1024 * 1024:
            return {"error": "Der PDF-Anhang darf höchstens 2 MB gross sein."}

    entry_id = db.log_correspondence(
        community_id=community_id,
        direction=direction,
        channel=channel,
        counterparty=(counterparty or "").strip(),
        subject=(subject or "").strip(),
        notes=(notes or "").strip(),
        logged_by=building_id,
        attachment_filename=attachment_filename,
        attachment_mime="application/pdf" if attachment_data else "",
        attachment_data=attachment_data,
    )
    if entry_id is None:
        return {"error": "Eintrag ungültig (Richtung oder Kanal unbekannt)."}
    return {"error": None, "entry_id": entry_id}


def leg_correspondence_attachment(
    entry_id: int, community_id: str, building_id: str
) -> dict | None:
    """Return a correspondence attachment only to a current LEG member."""
    status = formation_wizard.get_community_status(community_id)
    if not status or not any(
        member["building_id"] == building_id for member in status["members"] or []
    ):
        return None
    return db.get_correspondence_attachment(entry_id, community_id)


def leg_demo_overview() -> dict:
    """Fake, click-through LEG operator dashboard data for demos."""
    community = {
        "community_id": "demo-leg",
        "name": "LEG Musterweg",
        "status": "formation_started",
        "distribution_model": "proportional",
        "member_count": {"total": 5, "confirmed": 4, "invited": 1},
        "readiness_score": 60,
        "members": [
            {
                "building_id": "demo-building",
                "role": "admin",
                "status": "confirmed",
                "address": "Musterweg 1, 5400 Baden",
            },
            {
                "building_id": "demo-2",
                "role": "member",
                "status": "confirmed",
                "address": "Musterweg 3, 5400 Baden",
            },
            {
                "building_id": "demo-3",
                "role": "member",
                "status": "confirmed",
                "address": "Musterweg 5, 5400 Baden",
            },
            {
                "building_id": "demo-4",
                "role": "member",
                "status": "confirmed",
                "address": "Musterweg 7, 5400 Baden",
            },
            {
                "building_id": "demo-5",
                "role": "member",
                "status": "invited",
                "address": "Musterweg 9, 5400 Baden",
            },
        ],
        "documents": None,
    }
    community["next_steps"] = formation_wizard._get_next_steps(
        community["status"], community["member_count"]["confirmed"]
    )
    return {
        "error": None,
        "viewer_building_id": "demo-building",
        "is_admin": True,
        "minimum_community_size": formation_wizard.FORMATION_CONFIG[
            "min_community_size"
        ],
        "community": _with_german_labels(community),
    }


def demo_readiness() -> dict:
    """Fake, click-through dashboard data for demos."""
    return {
        "user": {
            "building_id": "demo-building",
            "address": "Mellingerstrasse 12, 5400 Baden",
            "annual_consumption_kwh": 4200,
            "potential_pv_kwp": 8.5,
            "referral_count": 4,
        },
        "readiness_score": 75,
        "checks": [
            ("E-Mail bestätigt", True),
            ("Verbrauchsdaten hinterlegt", True),
            ("EVU-Einwilligung erteilt", False),
            ("Nachbar-Einwilligung erteilt", True),
        ],
        "neighbor_count": 18,
        "neighbor_box_half_width_m": int(db.NEIGHBOR_BOX_HALF_WIDTH_KM * 1000),
        "referral_link": "https://openleg.ch/?ref=DEMO-LEG",
        "error": None,
    }


def update_profile(
    building_id: str,
    *,
    annual_consumption_kwh: str | None = None,
    potential_pv_kwp: str | None = None,
    share_with_utility: bool = False,
    share_with_neighbors: bool = False,
) -> dict:
    """Validate and delegate a dashboard profile update.

    Returns ``{"error": None}`` on success or ``{"error": "..."}``
    when validation fails.  Invalid input never reaches the store.
    """
    try:
        consumption = (
            float(annual_consumption_kwh)
            if annual_consumption_kwh not in (None, "")
            else None
        )
    except (TypeError, ValueError):
        return {"error": "Bitte geben Sie einen gültigen Jahresverbrauch ein."}

    if (
        consumption is None
        or not math.isfinite(consumption)
        or consumption <= 0
        or consumption > 9_999_999_999.99
    ):
        return {"error": "Bitte geben Sie einen gültigen Jahresverbrauch ein."}

    try:
        pv = float(potential_pv_kwp) if potential_pv_kwp not in (None, "") else None
    except (TypeError, ValueError):
        return {"error": "Bitte geben Sie eine gültige Solarleistung ein."}

    if pv is not None and (not math.isfinite(pv) or pv < 0 or pv > 999_999.99):
        return {"error": "Bitte geben Sie eine gültige Solarleistung ein."}

    saved = db.update_dashboard_profile(
        building_id,
        annual_consumption_kwh=consumption,
        potential_pv_kwp=pv,
        share_with_utility=share_with_utility,
        share_with_neighbors=share_with_neighbors,
    )
    if not saved:
        return {
            "error": "Das Energieprofil konnte nicht gespeichert werden. "
            "Bitte versuchen Sie es erneut."
        }
    return {"error": None}
