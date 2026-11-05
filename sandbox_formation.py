# SPDX-License-Identifier: AGPL-3.0-or-later
"""Self-guided formation with external actions confined to a village worker."""

import io
import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import abort, redirect, render_template, request, send_file

import community_access
import database as db
import vnb_exchange
from dashboard_routes import (
    _dashboard_csrf_token,
    _require_dashboard_csrf,
    _require_dashboard_session,
)
from i18n import gettext as _


class SimulationAdapter:
    """Return labelled local receipts instead of contacting a network operator."""

    def __init__(self, state="delivered"):
        if state not in {"delivered", "acknowledged", "rejected"}:
            raise ValueError("unsupported simulated outcome")
        self.state = state

    def contract(self):
        return vnb_exchange.ExchangeContract(
            "sonnwil-simulation",
            vnb_exchange.SUPPORTED_CONTRACT_VERSION,
            frozenset(
                {
                    vnb_exchange.FORMATION_SUBMISSION,
                    vnb_exchange.PARTICIPANT_JOIN,
                    vnb_exchange.PARTICIPANT_EXIT,
                    vnb_exchange.PARTICIPANT_CHANGE,
                }
            ),
        )

    def submit(self, package, *, idempotency_key):
        return self._outcome(idempotency_key)

    def submit_mutation(self, mutation, *, idempotency_key):
        return self._outcome(idempotency_key)

    def _outcome(self, key):
        return vnb_exchange.DeliveryResult(
            self.state,
            "SIMULATION-" + key[:24],
            self.state,
            response_evidence(self.state),
        )


def response_evidence(state):
    return json.dumps(
        {
            "simulation": "SIMULATION SONNWIL",
            "state": state,
            "start_authorization": False,
        },
        sort_keys=True,
    ).encode()


def _member(community, actor):
    return next(
        (
            item
            for item in (community or {}).get("members", [])
            if item["building_id"] == actor
        ),
        None,
    )


def _authorized(cid, capability):
    actor = _require_dashboard_session()
    _require_dashboard_csrf()
    community = db.fetch_community_with_members(cid, actor)
    member = _member(community, actor)
    if not community_access.allows(member, capability):
        abort(403)
    return actor, community


def install(web):
    """Install simulation controls only in an explicitly isolated worker."""
    if os.environ.get("OPENLEG_SANDBOX_WORKER") != "1":
        raise RuntimeError("formation simulation requires a village worker")
    # The worker owns this registry. Ordinary product controls use the same
    # domain command, with a local delivery boundary instead of manual export.
    vnb_exchange.ADAPTERS["manual-handover"] = SimulationAdapter()
    vnb_exchange.ADAPTERS["sonnwil-simulation"] = SimulationAdapter()

    @web.after_request
    def capture_invitation(response):
        """Capture an ordinary successful invitation without delivering mail."""
        if (
            request.method != "POST"
            or response.status_code != 302
            or not (request.endpoint or "").endswith(".leg_community_invite")
        ):
            return response
        from dashboard_routes import _dashboard_session_building_id
        from sandbox_mail import capture

        cid = request.view_args["community_id"]
        actor = _dashboard_session_building_id()
        community = db.fetch_community_with_members(cid, actor) or {}
        email = request.form.get("invite_email", "").strip().lower()
        for member in community.get("members", []):
            if (
                member["status"] == "invited"
                and (member.get("email") or "").lower() == email
            ):
                capture(
                    email,
                    _("SIMULATION SONNWIL: LEG-Einladung"),
                    _(
                        "Wechseln Sie zum eingeladenen Konto und bestätigen Sie die Teilnahme im LEG-Dashboard. Diese Einladung ist erfunden."
                    )
                    + "\n"
                    + os.environ["APP_BASE_URL"].rstrip("/")
                    + "/leg/dashboard?cid="
                    + cid,
                    event_key="formation-invite:" + cid + ":" + member["building_id"],
                )
        return response

    @web.get("/sandbox/formation")
    def sandbox_formation():
        actor = _require_dashboard_session()
        cid = request.args.get("cid", "")
        community = db.fetch_community_with_members(cid, actor) if cid else None
        if cid and not community:
            abort(404)
        bundle = None
        signature_notice = None
        if community and community["status"] in {
            "documents_generated",
            "signatures_pending",
        }:
            try:
                bundle = db.formation_signature_bundle(cid, actor)
            except db.SignatureConflict as error:
                signature_notice = str(error)
            except db.SignatureDenied:
                pass
        member = _member(community, actor)
        today = datetime.now(ZoneInfo("Europe/Zurich")).date()
        return render_template(
            "sandbox/formation.html",
            community=community,
            bundle=bundle,
            signature_notice=signature_notice,
            cases=db.list_vnb_submission_cases(cid) if community else [],
            can_manage_documents=community_access.allows(
                member,
                community_access.MANAGE_DOCUMENTS,
            ),
            can_manage_members=community_access.allows(
                member, community_access.MANAGE_MEMBERS
            ),
            join_target=_member(community, "sonnwil-operator"),
            mutations=db.list_vnb_mutations(cid) if community else [],
            correspondence=db.list_correspondence(cid) if community else [],
            join_date=(today + timedelta(days=7)).isoformat(),
            communities=db.fetch_user_communities(actor) or [],
            csrf_token=_dashboard_csrf_token(),
            today=today.isoformat(),
            state_labels={
                "delivered": _("Zugestellt"),
                "acknowledged": _("Empfang bestätigt"),
                "rejected": _("Abgelehnt"),
                "claimed": _("Übermittlung vorbereitet"),
                "failed": _("Übermittlung fehlgeschlagen"),
            },
            is_admin=community_access.is_administrator(
                member if member and member["status"] == "confirmed" else None
            ),
        )

    @web.post("/sandbox/formation/<cid>/signatures")
    def sandbox_signatures(cid):
        actor = _require_dashboard_session()
        _require_dashboard_csrf()
        if request.form.get("confirm") != "yes":
            abort(400)
        try:
            db.record_formation_signature(
                cid,
                actor,
                request.form.get("bundle", ""),
                "sonnwil-simulation",
                b"SIMULATION: no legally effective signature",
            )
        except db.SignatureDenied:
            abort(403)
        except db.SignatureConflict:
            abort(409)
        return redirect("/sandbox/formation?cid=" + cid)

    @web.post("/sandbox/formation/<cid>/vnb")
    def sandbox_vnb(cid):
        actor, _community = _authorized(cid, community_access.MANAGE_DOCUMENTS)
        state = request.form.get("state", "")
        if state not in {"delivered", "acknowledged", "rejected"}:
            abort(400)
        existing = db.list_vnb_submission_cases(cid)
        try:
            if existing:
                case = existing[0]
                if case["adapter_key"] != "sonnwil-simulation":
                    abort(409)
                if state == "delivered" and case["state"] == "delivered":
                    return redirect("/sandbox/formation?cid=" + cid)
                if request.form.get("case_id") != case["case_id"]:
                    abort(409)
                db.record_vnb_submission_response(
                    cid, case["case_id"], state, response_evidence(state)
                )
            else:
                vnb_exchange.submit_formation(
                    vnb_exchange.FormationSubmission(cid, actor),
                    SimulationAdapter(state),
                )
        except vnb_exchange.FormationSubmissionForbidden:
            abort(403)
        except (vnb_exchange.FormationSubmissionInvalid, db.VnbSubmissionConflict):
            abort(409)
        return redirect("/sandbox/formation?cid=" + cid)

    @web.get("/sandbox/formation/<cid>/start-evidence.pdf")
    def sandbox_start_evidence(cid):
        actor = _require_dashboard_session()
        community = db.fetch_community_with_members(cid, actor)
        member = _member(community, actor)
        if (
            not member
            or member["status"] != "confirmed"
            or not community_access.is_administrator(member)
        ):
            abort(403)
        if community["status"] not in {"dso_submitted", "dso_approved", "active"}:
            abort(409)
        import document_generator

        markup = render_template(
            "sandbox/start_evidence.html",
            community=community,
            today=datetime.now(ZoneInfo("Europe/Zurich")).date().isoformat(),
        )
        return send_file(
            io.BytesIO(document_generator.render_pdf_html(markup)),
            mimetype="application/pdf",
            as_attachment=True,
            download_name="simulation-sonnwil-start.pdf",
        )

    @web.get("/sandbox/formation/<cid>/join-agreement.pdf")
    def sandbox_join_agreement(cid):
        actor = _require_dashboard_session()
        community = db.fetch_community_with_members(cid, actor)
        member = _member(community, actor)
        target = _member(community, request.args.get("participant", ""))
        if not target or (
            actor != target["building_id"]
            and not community_access.allows(member, community_access.MANAGE_MEMBERS)
        ):
            abort(404)
        if community["status"] != "active" or target["status"] not in {
            "invited",
            "confirmed",
        }:
            abort(409)
        profile = db.get_building_for_dashboard(target["building_id"]) or {}
        import document_generator

        pdf = document_generator.generate_teilnehmervertrag(
            target.get("email") or target["building_id"],
            target.get("address") or "",
            community["name"],
            "producer" if (profile.get("potential_pv_kwp") or 0) > 0 else "consumer",
            pv_kwp=profile.get("potential_pv_kwp") or 0,
            annual_consumption_kwh=profile.get("annual_consumption_kwh") or 0,
        )
        return send_file(
            io.BytesIO(pdf),
            mimetype="application/pdf",
            as_attachment=True,
            download_name="simulation-sonnwil-join.pdf",
        )

    @web.post("/sandbox/formation/<cid>/mutation-response")
    def sandbox_mutation_response(cid):
        _authorized(cid, community_access.MANAGE_MEMBERS)
        state = request.form.get("state", "")
        if state not in {"acknowledged", "rejected"}:
            abort(400)
        case = db.get_vnb_mutation(cid, request.form.get("mutation_id", ""))
        if not case or case["adapter_key"] != "sonnwil-simulation":
            abort(404)
        if case["state"] not in {"delivered", state}:
            abort(409)
        db.record_vnb_mutation_response(
            cid,
            case["case_id"],
            state,
            case["external_request_id"],
            state,
            response_evidence(state),
        )
        return redirect("/sandbox/formation?cid=" + cid)
