# SPDX-License-Identifier: AGPL-3.0-or-later
"""Self-guided formation with external actions confined to a village worker."""

import io
import json
import os
from datetime import datetime
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


def _authorized(cid, capability):
    actor = _require_dashboard_session()
    _require_dashboard_csrf()
    community = db.fetch_community_with_members(cid, actor)
    member = next(
        (
            item
            for item in (community or {}).get("members", [])
            if item["building_id"] == actor
        ),
        None,
    )
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

    @web.get("/sandbox/formation")
    def sandbox_formation():
        actor = _require_dashboard_session()
        cid = request.args.get("cid", "")
        community = db.fetch_community_with_members(cid, actor) if cid else None
        if cid and not community:
            abort(404)
        bundle = None
        signature_notice = None
        if community:
            try:
                bundle = db.formation_signature_bundle(cid, actor)
            except db.SignatureConflict as error:
                signature_notice = str(error)
            except db.SignatureDenied:
                pass
        return render_template(
            "sandbox/formation.html",
            community=community,
            bundle=bundle,
            signature_notice=signature_notice,
            cases=db.list_vnb_submission_cases(cid) if community else [],
            can_manage_documents=community_access.allows(
                next(
                    (
                        item
                        for item in (community or {}).get("members", [])
                        if item["building_id"] == actor
                    ),
                    None,
                ),
                community_access.MANAGE_DOCUMENTS,
            ),
            communities=db.fetch_user_communities(actor) or [],
            csrf_token=_dashboard_csrf_token(),
            today=datetime.now(ZoneInfo("Europe/Zurich")).date().isoformat(),
            is_admin=community_access.is_administrator(
                next(
                    (
                        item
                        for item in (community or {}).get("members", [])
                        if item["building_id"] == actor
                        and item["status"] == "confirmed"
                    ),
                    None,
                )
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
        member = next(
            (
                item
                for item in (community or {}).get("members", [])
                if item["building_id"] == actor
            ),
            None,
        )
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
