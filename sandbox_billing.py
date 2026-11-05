# SPDX-License-Identifier: AGPL-3.0-or-later
"""A local trigger for the ordinary guarded billing runner."""

import gzip
import io
import os
import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from flask import abort, jsonify, redirect, render_template, request, send_file
from psycopg2 import Error as DatabaseError

import billing_runner
import community_access
import database as db
from dashboard_routes import (
    _dashboard_csrf_token,
    _require_dashboard_csrf,
    _require_dashboard_session,
)
from sandbox_metering_fixtures import COMMUNITY, fixture
from translation_markers import gettext_noop as _N

START = datetime(2026, 9, 1, tzinfo=ZoneInfo("Europe/Zurich"))
END = datetime(2026, 10, 1, tzinfo=ZoneInfo("Europe/Zurich"))
DIAGNOSTICS = {
    "billing_tariff_missing": _N(
        "Speichern Sie zuerst eine vollständige Richtlinie für September."
    ),
    "vnb_evidence_missing": _N(
        "Importieren Sie die vollständige gültige Septemberquelle. Eine zurückgezogene Quelle genügt nicht."
    ),
    "billing_inputs_changed": _N(
        "Die Eingaben dieses Entwurfs wurden verändert. Der vorhandene Entwurf und ausgestellte Rechnungen werden nicht überschrieben."
    ),
    "vnb_allocation_mismatch": _N(
        "Die Quelle und die unterstützte Verteilung stimmen nicht überein. Für diesen Fall gibt es keine Freigabe-Umgehung."
    ),
    "metering_readings_invalid": _N(
        "Prüfen Sie die vollständigen datierten Messwerte und die aktuelle Mitgliedschaft."
    ),
    "battery_configuration_invalid": _N(
        "Die Akku-Konfiguration ist unvollständig oder passt nicht zu den abgerechneten Mitgliedern."
    ),
    "billing_store_unavailable": _N(
        "Die Abrechnungsablage ist nicht verfügbar. Versuchen Sie es später erneut."
    ),
    "billing_provenance_missing": _N(
        "Den Messwerten fehlt ein nachvollziehbarer Importbeleg."
    ),
    "participant_registration_changed": _N(
        "Eine ursprüngliche Teilnehmerregistrierung fehlt oder wurde ersetzt. Klären Sie die Zuordnung vor einer neuen Abrechnung."
    ),
    "billing_run_failed": _N(
        "Der gewöhnliche Abrechnungslauf hat diesen Entwurf abgelehnt. Prüfen Sie Richtlinie und Quelle."
    ),
}


def _authorize(*, write=False):
    """Require current billing preparation authority and CSRF on writes."""
    actor = _require_dashboard_session()
    if write:
        _require_dashboard_csrf()
    community = db.fetch_community_with_members(COMMUNITY, actor)
    member = next(
        (m for m in (community or {}).get("members", []) if m["building_id"] == actor),
        None,
    )
    if not community_access.allows(member, community_access.PREPARE_BILLING):
        abort(403)
    return actor


def _present(error=None, status=200):
    """Render the September policy, accepted evidence and billing draft."""
    policy = source = period = None
    if error != "billing_store_unavailable":
        try:
            policy = db.get_billing_policy(COMMUNITY, START, END)
            source = db.get_validated_calculated_values(COMMUNITY, START, END)
            existing = db.get_billing_period_for_window(COMMUNITY, START, END)
            period = (
                db.get_billing_period(existing["id"], COMMUNITY) if existing else None
            )
        except (db.BillingStoreError, DatabaseError):
            policy = source = period = None
            error, status = "billing_store_unavailable", 503
    quantities = {"consumption": {}, "production": {}}
    for row in (source or {}).get("normalized_records", []):
        totals = quantities[row["direction"]]
        for key, value in row["source_quantities"].items():
            totals[key] = totals.get(key, Decimal(0)) + Decimal(value)
    return render_template(
        "sandbox/billing.html",
        csrf_token=_dashboard_csrf_token(),
        community_id=COMMUNITY,
        policy=policy,
        source=source,
        period=period,
        quantities=quantities,
        error=error,
        diagnostic=DIAGNOSTICS.get(error),
    ), status


def install(web):
    """Install fictional billing controls only in a private village worker."""
    if os.environ.get("OPENLEG_SANDBOX_WORKER") != "1":
        raise RuntimeError("billing simulation requires a village worker")

    @web.after_request
    def label_simulated_exports(response):
        """Label successful profile and community JSON exports as simulations."""
        endpoint = (request.endpoint or "").rsplit(".", 1)[-1]
        if response.status_code == 200 and endpoint in {
            "dashboard_profile_export",
            "leg_community_archive_export",
        }:
            response.direct_passthrough = False
            payload = response.get_json()
            if isinstance(payload, dict):
                container = payload.get("manifest", payload)
                container["simulation"] = (
                    "SIMULATION SONNWIL - fictional data, no payment due"
                )
                response.set_data(jsonify(payload).get_data())
                response.headers["Content-Type"] = "application/json"
        return response

    @web.get("/sandbox/billing")
    def sandbox_billing():
        """Show the billing exercise after checking preparation authority."""
        _authorize()
        return _present()

    @web.post("/sandbox/billing/prepare")
    def sandbox_billing_prepare():
        """Prepare a confirmed draft through the ordinary billing runner."""
        actor = _authorize(write=True)
        if request.form.get("confirm") != "yes":
            abort(400)
        try:
            billing_runner.run_billing_period(COMMUNITY, START, END, prepared_by=actor)
        except billing_runner.BillingRunError as exc:
            return _present(
                exc.code, 503 if exc.code == "billing_store_unavailable" else 409
            )
        return redirect("/sandbox/billing", code=303)

    @web.get("/sandbox/billing/fixtures/unequal.xml.gz")
    def sandbox_billing_unequal_fixture():
        """Download an unequal synthetic source for exercising allocation refusal."""
        _authorize()
        source = ET.fromstring(gzip.decompress(fixture("valid")))
        source.find(
            "ValidatedMeteredData_HeaderInformation/InstanceDocument/DocumentID"
        ).text = "SONNWIL-SIM-UNEQUAL"
        for block in source.findall("MeteringData"):
            if block.find("ProductionMeteringPoint") is None:
                continue
            value = {"2404050010123": "0.400", "2404050010124": "0.100"}.get(
                block.findtext("Product/ID/VSENationalCode")
            )
            if value:
                for observation in block.findall("Observation/Volume"):
                    observation.text = value
        evidence = (
            b"<!-- SIMULATION SONNWIL - unapproved unequal totals -->"
            + ET.tostring(source)
        )
        return send_file(
            io.BytesIO(gzip.compress(evidence, mtime=0)),
            mimetype="application/gzip",
            as_attachment=True,
            download_name="simulation-sonnwil-unequal.xml.gz",
        )
