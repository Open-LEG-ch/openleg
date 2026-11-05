# SPDX-License-Identifier: AGPL-3.0-or-later
"""Browser controls over ordinary metering commands in one private village."""

import gzip
import hashlib
import io
import json
import os
import re
import secrets
import zlib
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import abort, redirect, render_template, request, send_file

import community_access
import database as db
import e66_evidence
import sdat_ingestion
from dashboard_routes import (
    _dashboard_csrf_token,
    _require_dashboard_csrf,
    _require_dashboard_session,
)
from sandbox_metering_fixtures import (
    COMMUNITY,
    EXTERNAL_COMMUNITY,
    KINDS,
    TERRITORY,
    fixture,
)
from translation_markers import gettext_noop as _N

MAX_XML_BYTES = 8 * 1024 * 1024
DIAGNOSTICS = {
    "incomplete_e66_period": _N(
        "Ein Viertelstundenwert fehlt. Laden Sie die vollständige gültige Lieferung, statt Werte zu ergänzen."
    ),
    "e66_mapping_unavailable": _N(
        "Ein Messpunkt ist nicht zugeordnet. Prüfen Sie die datierte Zuordnung; die Lieferung bleibt abgelehnt."
    ),
    "invalid_e66_precision": _N(
        "Die Quelle enthält feinere Werte als das unterstützte E66-Profil. Verwenden Sie die gültige Datei ohne Rundung."
    ),
    "e66_replacement_requires_withdrawal": _N(
        "Ziehen Sie zuerst die angenommene Originalquelle ausdrücklich zurück. Importieren Sie danach die Korrektur."
    ),
    "overlapping_delivery": _N(
        "Für dieses Zeitfenster liegt bereits eine Quelle vor. Prüfen Sie das Original und ziehen Sie es vor einer Korrektur zurück."
    ),
    "invalid_e66": _N(
        "Die Datei ist kein unterstützter vollständiger E66-Beleg. Verwenden Sie eine der bereitgestellten Übungsdateien."
    ),
    "source_outside_community": _N(
        "Diese Quelle gehört nicht zur vorbereiteten LEG Sonnwil."
    ),
    "source_too_large": _N("Die entpackte Datei überschreitet das Limit von 8 MiB."),
}
FIXTURE_LABELS = {
    "valid": _N("Vollständiger September"),
    "duplicate": _N("Identische Wiederholung"),
    "corrected": _N("Korrektur nach Rückzug"),
    "missing": _N("Fehlende Viertelstunde"),
    "unmapped": _N("Nicht zugeordneter Messpunkt"),
    "precision": _N("Nicht unterstützte Genauigkeit"),
}


def _authorize(*, write=False):
    actor = _require_dashboard_session()
    if write:
        _require_dashboard_csrf()
    community = db.fetch_community_with_members(COMMUNITY, actor)
    member = next(
        (m for m in (community or {}).get("members", []) if m["building_id"] == actor),
        None,
    )
    if not community_access.allows(member, community_access.MANAGE_METERING):
        abort(403)
    return actor


def _intake(evidence):
    """Keep parsing, validation and the atomic evidence write in production seams."""
    document = e66_evidence.parse_source(evidence)
    if document["vnb_community_id"] != EXTERNAL_COMMUNITY:
        raise e66_evidence.E66EvidenceError("source_outside_community")
    return db.ingest_e66_calculated_values(TERRITORY, document, evidence)


def _expand(data):
    if data.startswith(b"\x1f\x8b"):
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as source:
            data = source.read(MAX_XML_BYTES + 1)
    if len(data) > MAX_XML_BYTES:
        raise e66_evidence.E66EvidenceError("source_too_large")
    return data


def _attempts():
    directory = Path.cwd() / "metering-attempts"
    directory.mkdir(mode=0o700, exist_ok=True)
    return directory


def _record_attempt(name, evidence, result):
    """Keep safe diagnostics and source identity, without inventing accepted evidence."""
    fields = (
        "id",
        "status",
        "replayed",
        "record_count",
        "fingerprint",
        "lifecycle_state",
    )
    saved = {key: result[key] for key in fields if key in result}
    saved.update(
        name=name,
        attempted_at=datetime.now(UTC).isoformat(),
        evidence_sha256=hashlib.sha256(evidence).hexdigest(),
        diagnostics=result.get("diagnostics", []),
    )
    key = secrets.token_hex(16)
    directory = _attempts()
    pending = directory / (key + ".pending")
    pending.write_text(json.dumps(saved, default=str))
    pending.replace(directory / (key + ".json"))
    return key


def _run_local(territory, schedule, fail_source=False):
    """Use the ordinary scheduler, lock, retry and report with a local transport."""

    def fetch(directory):
        if fail_source:
            raise sdat_ingestion.IngestionError("fetch_failed")
        target = Path(directory).resolve()
        root = (Path.cwd() / "data" / "sdat").resolve()
        if target != root / TERRITORY:
            raise sdat_ingestion.IngestionError("invalid_local_dir")
        target.mkdir(mode=0o700, parents=True, exist_ok=True)
        source = target / "simulation-sonnwil-valid.xml.gz"
        existed = source.exists()
        if not existed:
            pending = source.with_suffix(".pending")
            pending.write_bytes(fixture("valid"))
            pending.replace(source)
        return {
            "downloaded": [] if existed else [source.name],
            "skipped": [source.name] if existed else [],
        }

    def ingest(directory):
        source = Path(directory) / "simulation-sonnwil-valid.xml.gz"
        result = _intake(_expand(source.read_bytes()))
        if result["status"] != "accepted":
            raise sdat_ingestion.IngestionError("import_failed")
        replayed = bool(result.get("replayed"))
        return {
            "files_imported": 0 if replayed else 1,
            "files_existing": 1 if replayed else 0,
            "readings_imported": 0 if replayed else result["record_count"],
        }

    return sdat_ingestion.run(territory, schedule, fetcher=fetch, importer=ingest)


def _present(selected=None, status=200):
    attempts = [
        json.loads(path.read_text())
        for path in sorted(
            _attempts().glob("*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )[:10]
    ]
    result = None
    if selected:
        if not re.fullmatch("[a-f0-9]{32}", selected):
            abort(404)
        path = _attempts() / (selected + ".json")
        if not path.is_file():
            abort(404)
        result = json.loads(path.read_text())
    deliveries = [
        row
        for row in db.list_calculated_values_deliveries(TERRITORY)
        if row["community_id"] == COMMUNITY
    ]
    points = db.get_metering_points(COMMUNITY)
    for point in points:
        point["readings"] = db.get_metering_point_reading_stats(
            point["metering_point_id"]
        )
    return render_template(
        "sandbox/metering.html",
        csrf_token=_dashboard_csrf_token(),
        result=result,
        attempts=attempts,
        deliveries=deliveries,
        fixture_labels=FIXTURE_LABELS,
        diagnostics=DIAGNOSTICS,
        points=points,
        zurich=ZoneInfo("Europe/Zurich"),
        schedules=[
            item
            for item in db.list_sdat_ingestion_schedules()
            if item["territory"] == TERRITORY
        ],
    ), status


def install(web):
    """Install no upload or simulated-source controls in the normal application."""
    if os.environ.get("OPENLEG_SANDBOX_WORKER") != "1":
        raise RuntimeError("metering simulation requires a village worker")

    @web.get("/sandbox/metering")
    def sandbox_metering():
        _authorize()
        return _present(request.args.get("attempt"))

    @web.get("/sandbox/metering/fixtures/<kind>.xml.gz")
    def sandbox_metering_fixture(kind):
        _authorize()
        if kind not in KINDS:
            abort(404)
        return send_file(
            io.BytesIO(fixture(kind)),
            mimetype="application/gzip",
            as_attachment=True,
            download_name="simulation-sonnwil-" + kind + ".xml.gz",
        )

    @web.post("/sandbox/metering/import")
    def sandbox_metering_import():
        _authorize(write=True)
        upload = request.files.get("delivery")
        if not upload or not upload.filename:
            abort(400)
        evidence = upload.read()
        try:
            evidence = _expand(evidence)
            result = _intake(evidence)
        except (OSError, EOFError, UnicodeError, zlib.error):
            result = {"status": "rejected", "diagnostics": [{"code": "invalid_e66"}]}
        except e66_evidence.E66EvidenceError as error:
            code = str(error) if str(error) in DIAGNOSTICS else "invalid_e66"
            result = {"status": "rejected", "diagnostics": [{"code": code}]}
        key = _record_attempt(Path(upload.filename).name, evidence, result)
        if result["status"] != "accepted":
            return _present(key, 422)
        return redirect("/sandbox/metering?attempt=" + key, code=303)

    @web.post("/sandbox/metering/withdraw/<int:delivery_id>")
    def sandbox_metering_withdraw(delivery_id):
        _authorize(write=True)
        if request.form.get("confirm") != "yes":
            abort(400)
        known = next(
            (
                item
                for item in db.list_calculated_values_deliveries(TERRITORY)
                if item["id"] == delivery_id and item["community_id"] == COMMUNITY
            ),
            None,
        )
        if not known:
            abort(404)
        try:
            db.withdraw_calculated_values_delivery(
                TERRITORY,
                delivery_id,
                "sonnwil-withdraw-" + str(delivery_id),
                request.form.get("reason", ""),
            )
        except db.CalculatedValuesLifecycleMissing:
            abort(404)
        except db.CalculatedValuesLifecycleConflict:
            abort(409)
        except ValueError:
            abort(400)
        return redirect("/sandbox/metering", code=303)

    @web.post("/sandbox/metering/jobs")
    def sandbox_metering_jobs():
        _authorize(write=True)
        action = request.form.get("action")
        if action not in {"due", "retry"}:
            abort(400)
        schedule = next(
            (
                item
                for item in db.list_sdat_ingestion_schedules()
                if item["territory"] == TERRITORY
            ),
            None,
        )
        if schedule is None:
            schedule = db.upsert_sdat_ingestion_schedule(
                TERRITORY,
                {
                    "enabled": True,
                    "timezone": "Europe/Zurich",
                    "local_time": "00:00",
                    "max_attempts": 1,
                    "retry_seconds": 0,
                },
            )
        fail_source = request.form.get("fail_source") == "yes"
        if action == "due":
            sdat_ingestion.run_due(
                [schedule],
                runner=lambda territory, configuration: _run_local(
                    territory, configuration, fail_source
                ),
            )
        else:
            _run_local(TERRITORY, schedule, fail_source)
        return redirect("/sandbox/metering", code=303)
