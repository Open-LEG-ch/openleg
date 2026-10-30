# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reviewed advisory values and conservative device matching, without SQL."""

import hashlib
import json
import re
import unicodedata
from datetime import UTC, date, datetime, timedelta

from equipment import EquipmentError, validate_public_source_url
from translation_markers import gettext_noop as _N

MAX_BYTES = 32_768
MAX_CATALOG = 200
MAX_REVISIONS = 50
MAX_POLICY_REVISIONS = 1000
MAX_SOURCE_REVIEWS = 10_000
CLASSIFICATIONS = {
    "affected": _N("Im betroffenen Firmwarebereich"),
    "possible": _N("Möglicherweise betroffen / unbekannt"),
    "not_within": _N("Ausserhalb dieses Firmwarebereichs"),
    "no_identity_match": _N("Keine genaue Gerätezuordnung"),
}
REASONS = {
    "firmware_within_range": _N(
        "Die erfasste Firmware liegt im veröffentlichten Bereich."
    ),
    "firmware_outside_range": _N(
        "Die erfasste Firmware liegt ausserhalb des Bereichs dieser Meldung."
    ),
    "missing_identity": _N(
        "Hersteller oder Modell fehlen. Die Zuordnung bleibt offen."
    ),
    "identity_unresolved": _N(
        "Hersteller oder Modell stimmen mit keiner geprüften Bezeichnung oder Alias überein."
    ),
    "missing_firmware": _N("Die Firmware fehlt. Die Betroffenheit bleibt offen."),
    "unsupported_version": _N(
        "Diese Versionsangabe lässt sich mit dem geprüften Versionsschema nicht vergleichen."
    ),
}
REMEDY_STATUSES = {
    "unknown": _N("Unbekannt"),
    "planned": _N("Abklärung geplant"),
    "reported_done": _N("Als erledigt angegeben"),
}
SOURCE_KINDS = {
    "unknown": _N("Unbekannt"),
    "owner_report": _N("Eigene Angabe"),
    "device_observation": _N("Am Gerät abgelesen"),
    "installer_evidence": _N("Hinterlegter Installateurnachweis"),
}
CATALOG_STATES = {
    "disabled": _N("Hinweise ausgeschaltet"),
    "unreviewed": _N("Quellenprüfung ausstehend"),
    "failed": _N("Letzte Quellenprüfung fehlgeschlagen"),
    "stale": _N("Quellenprüfung überfällig"),
    "current": _N("Quellenprüfung aktuell"),
}


class AdvisoryError(ValueError):
    def __init__(self, code="invalid_advisory", status=400, field=None):
        self.code, self.status, self.field = code, status, field
        super().__init__(code)


def normalize_identity(value):
    return " ".join(unicodedata.normalize("NFKC", value or "").casefold().split())


def _object(value, fields, field):
    if not isinstance(value, dict) or set(value) - set(fields):
        raise AdvisoryError(field=field)
    return value


def _text(value, limit, field, *, optional=False):
    if optional and value in (None, ""):
        return None
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > limit
        or any(ord(char) < 32 and char not in "\n\t" for char in value)
    ):
        raise AdvisoryError(field=field)
    return value.strip()


def _choice(value, options, field):
    if not isinstance(value, str) or value not in options:
        raise AdvisoryError(field=field)
    return value


def _date(value, field, *, optional=False):
    if optional and value in (None, ""):
        return None
    try:
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError
        parsed = date.fromisoformat(value)
        if parsed > datetime.now(UTC).date():
            raise ValueError
    except ValueError:
        raise AdvisoryError(field=field) from None
    return value


def _url(value, field, *, optional=False):
    try:
        result = validate_public_source_url(value, field)
    except EquipmentError:
        raise AdvisoryError(field=field) from None
    if not result and not optional:
        raise AdvisoryError(field=field)
    return result


def _aliases(values, field):
    if not isinstance(values, list) or len(values) > 20:
        raise AdvisoryError(field=field)
    result = [_text(value, 120, field) for value in values]
    if len({normalize_identity(value) for value in result}) != len(result):
        raise AdvisoryError(field=field)
    return result


def parse_version(value, scheme):
    if not isinstance(value, str):
        return None
    pattern = (
        r"(0|[1-9]\d{0,4})\.(0|[1-9]\d{0,4})\.(0|[1-9]\d{0,4})\.R"
        if scheme == "sma-r"
        else r"(?:0|[1-9]\d{0,4})(?:\.(?:0|[1-9]\d{0,4})){0,3}"
    )
    if scheme not in ("sma-r", "dotted-numeric") or not re.fullmatch(
        pattern, value, flags=re.ASCII
    ):
        return None
    return tuple(int(part) for part in value.removesuffix(".R").split("."))


def _firmware(payload):
    payload = _object(
        payload,
        ("scheme", "lower_inclusive", "upper_exclusive", "range_text"),
        "firmware",
    )
    scheme = _choice(
        payload.get("scheme"),
        ("dotted-numeric", "sma-r", "unsupported"),
        "firmware.scheme",
    )
    lower = _text(
        payload.get("lower_inclusive"), 80, "firmware.lower_inclusive", optional=True
    )
    upper = _text(
        payload.get("upper_exclusive"), 80, "firmware.upper_exclusive", optional=True
    )
    if scheme == "unsupported":
        if lower or upper:
            raise AdvisoryError(field="firmware.scheme")
    else:
        parsed = [
            parse_version(value, scheme)
            for value in (lower, upper)
            if value is not None
        ]
        if (
            not parsed
            or any(value is None for value in parsed)
            or (
                len(parsed) == 2
                and (len(parsed[0]) != len(parsed[1]) or parsed[0] >= parsed[1])
            )
        ):
            raise AdvisoryError(field="firmware")
    return {
        "scheme": scheme,
        "lower_inclusive": lower,
        "upper_exclusive": upper,
        "range_text": _text(payload.get("range_text"), 500, "firmware.range_text"),
    }


def validate_entry(payload):
    payload = _object(
        payload,
        (
            "reference",
            "publisher",
            "source_url",
            "source_revision",
            "publication_date",
            "title",
            "summary",
            "vendor",
            "vendor_aliases",
            "models",
            "remedy",
            "status",
            "change_note",
        ),
        "entry",
    )
    result = {
        field: _text(payload.get(field), limit, field)
        for field, limit in (
            ("reference", 120),
            ("publisher", 120),
            ("source_revision", 120),
            ("title", 200),
            ("summary", 2000),
            ("vendor", 120),
            ("change_note", 1000),
        )
    }
    result["source_url"] = _url(payload.get("source_url"), "source_url")
    result["publication_date"] = _date(
        payload.get("publication_date"), "publication_date"
    )
    result["status"] = _choice(
        payload.get("status", "active"), ("active", "withdrawn"), "status"
    )
    result["vendor_aliases"] = _aliases(
        payload.get("vendor_aliases", []), "vendor_aliases"
    )
    if normalize_identity(result["vendor"]) in {
        normalize_identity(alias) for alias in result["vendor_aliases"]
    }:
        raise AdvisoryError(field="vendor_aliases")
    models = payload.get("models")
    if not isinstance(models, list) or not 1 <= len(models) <= 20:
        raise AdvisoryError(field="models")
    result["models"] = []
    seen = set()
    for model in models:
        model = _object(model, ("model", "aliases", "firmware"), "model")
        name = _text(model.get("model"), 120, "model")
        aliases = _aliases(model.get("aliases", []), "model.aliases")
        names = [normalize_identity(value) for value in [name, *aliases]]
        if len(set(names)) != len(names) or set(names) & seen:
            raise AdvisoryError(field="model.aliases")
        seen.update(names)
        result["models"].append(
            {
                "model": name,
                "aliases": aliases,
                "firmware": _firmware(model.get("firmware")),
            }
        )
    remedy = _object(payload.get("remedy"), ("text", "url"), "remedy")
    result["remedy"] = {
        "text": _text(remedy.get("text"), 2000, "remedy.text"),
        "url": _url(remedy.get("url"), "remedy.url", optional=True),
    }
    return result


def content_digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def equipment_context(equipment):
    return {key: equipment.get(key) for key in ("manufacturer", "model", "firmware")}


def match_advisory(equipment, entry):
    vendor = normalize_identity(equipment.get("manufacturer"))
    model_name = normalize_identity(equipment.get("model"))
    vendors = {
        normalize_identity(value)
        for value in [entry["vendor"], *entry["vendor_aliases"]]
    }
    models = [
        model
        for model in entry["models"]
        if model_name
        in {normalize_identity(value) for value in [model["model"], *model["aliases"]]}
    ]
    result = {
        "classification": "possible",
        "identity_matched": False,
        "reason": "missing_identity",
        "matched_model": models[0]["model"] if models else None,
        "firmware_rule": models[0]["firmware"] if models else None,
    }
    if (vendor and vendor not in vendors) or (model_name and not models):
        return {
            **result,
            "classification": "no_identity_match",
            "reason": "identity_unresolved",
        }
    if not vendor or not model_name:
        return result
    result["identity_matched"] = True
    rule = models[0]["firmware"]
    firmware = equipment.get("firmware")
    if not firmware:
        return {**result, "reason": "missing_firmware"}
    version = parse_version(firmware, rule["scheme"])
    bounds = [
        parse_version(rule[key], rule["scheme"])
        for key in ("lower_inclusive", "upper_exclusive")
    ]
    if version is None or any(
        bound is not None and len(version) != len(bound) for bound in bounds
    ):
        return {**result, "reason": "unsupported_version"}
    lower, upper = bounds
    within = (lower is None or version >= lower) and (upper is None or version < upper)
    return {
        **result,
        "classification": "affected" if within else "not_within",
        "reason": "firmware_within_range" if within else "firmware_outside_range",
    }


def validate_publication(payload, entry, digest):
    payload = _object(
        payload, ("digest", "reviewer_id", "reviewed_on", "approve"), "publication"
    )
    if payload.get("approve") is not True:
        raise AdvisoryError(field="approve")
    if payload.get("digest") != digest:
        raise AdvisoryError("revision_digest_changed", 409)
    reviewed = _date(payload.get("reviewed_on"), "reviewed_on")
    if reviewed < entry["publication_date"]:
        raise AdvisoryError(field="reviewed_on")
    return {
        "reviewer_id": _text(payload.get("reviewer_id"), 120, "reviewer_id"),
        "reviewed_on": reviewed,
    }


def validate_policy(payload):
    payload = _object(
        payload,
        ("enabled", "review_owner", "cadence_days", "coverage", "source_urls"),
        "policy",
    )
    enabled = payload.get("enabled", False)
    if not isinstance(enabled, bool):
        raise AdvisoryError(field="enabled")
    owner = _text(
        payload.get("review_owner"), 120, "review_owner", optional=not enabled
    )
    cadence = payload.get("cadence_days")
    if cadence is not None and (
        isinstance(cadence, bool)
        or not isinstance(cadence, int)
        or not 1 <= cadence <= 90
    ):
        raise AdvisoryError(field="cadence_days")
    if enabled and cadence is None:
        raise AdvisoryError(field="cadence_days")
    coverage = _text(payload.get("coverage"), 1000, "coverage", optional=not enabled)
    urls = payload.get("source_urls", [])
    if not isinstance(urls, list) or len(urls) > 20 or (enabled and not urls):
        raise AdvisoryError(field="source_urls")
    urls = [_url(url, "source_urls") for url in urls]
    if len(set(urls)) != len(urls):
        raise AdvisoryError(field="source_urls")
    return {
        "enabled": enabled,
        "review_owner": owner,
        "cadence_days": cadence,
        "coverage": coverage,
        "source_urls": urls,
    }


def validate_source_review(payload, policy):
    payload = _object(
        payload,
        (
            "policy_revision",
            "review_owner",
            "reviewed_on",
            "outcome",
            "source_urls",
            "failure_code",
        ),
        "source_review",
    )
    revision = payload.get("policy_revision")
    if (
        isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision != policy["revision"]
    ):
        raise AdvisoryError("source_policy_changed", 409)
    owner = _text(payload.get("review_owner"), 120, "review_owner")
    if not policy["review_owner"] or owner != policy["review_owner"]:
        raise AdvisoryError("source_review_owner_mismatch", 409)
    outcome = _choice(payload.get("outcome"), ("success", "failure"), "outcome")
    urls = payload.get("source_urls")
    if not isinstance(urls, list) or len(urls) > 20:
        raise AdvisoryError(field="source_urls")
    urls = [_url(url, "source_urls") for url in urls]
    if len(set(urls)) != len(urls) or set(urls) - set(policy["source_urls"]):
        raise AdvisoryError(field="source_urls")
    if outcome == "success" and (
        not policy["source_urls"] or set(urls) != set(policy["source_urls"])
    ):
        raise AdvisoryError(field="source_urls")
    failure = payload.get("failure_code")
    if (outcome == "success" and failure is not None) or (
        outcome == "failure"
        and failure
        not in ("fetch_failed", "format_changed", "source_missing", "manual_incomplete")
    ):
        raise AdvisoryError(field="failure_code")
    return {
        "policy_revision": revision,
        "review_owner": owner,
        "reviewed_on": _date(payload.get("reviewed_on"), "reviewed_on"),
        "outcome": outcome,
        "source_urls": urls,
        "failure_code": failure,
    }


def catalog_health(policy, reviews, *, today=None):
    today = today or datetime.now(UTC).date()
    latest = reviews[0] if reviews else None
    success = next(
        (review for review in reviews if review["outcome"] == "success"), None
    )
    success_date = date.fromisoformat(success["reviewed_on"]) if success else None
    due = (
        success_date + timedelta(days=policy["cadence_days"])
        if success_date and policy and policy["cadence_days"]
        else None
    )
    status = "disabled"
    if policy and policy["enabled"]:
        if latest and latest["outcome"] == "failure":
            status = "failed"
        elif not success:
            status = "unreviewed"
        else:
            status = "stale" if today >= due else "current"
    return {
        "status": status,
        "coverage": policy["coverage"] if policy else None,
        "source_urls": policy["source_urls"] if policy else [],
        "cadence_days": policy["cadence_days"] if policy else None,
        "last_successful_review": success_date.isoformat() if success_date else None,
        "next_review_due": due.isoformat() if due else None,
        "last_attempt": {
            key: latest[key] for key in ("reviewed_on", "outcome", "failure_code")
        }
        if latest
        else None,
    }


def revision_state(entry_status, published, revision, latest_published=0):
    if not published:
        return "draft"
    return "superseded" if revision < latest_published else entry_status


def assess_catalog(
    equipment, publications, actions, health, *, history_truncated=False
):
    context = equipment_context(equipment)
    context_digest = content_digest(context)
    latest = {}
    for publication in publications:
        advisory_id = publication["advisory_id"]
        latest[advisory_id] = max(latest.get(advisory_id, 0), publication["revision"])
    action_index = {
        (action["advisory_id"], action["revision"], action["context_digest"]): action
        for action in actions
    }
    assessments, history = [], []
    for publication in publications:
        advisory_id, revision = publication["advisory_id"], publication["revision"]
        entry = publication["entry"]
        match = match_advisory(equipment, entry)
        if match["classification"] == "no_identity_match":
            continue
        state = revision_state(entry["status"], True, revision, latest[advisory_id])
        source_covered = entry["source_url"] in health["source_urls"]
        action = action_index.get((advisory_id, revision, context_digest))
        item = {
            "advisory_id": advisory_id,
            "revision": revision,
            "entry": entry,
            "reviewed_on": publication["review"]["reviewed_on"],
            "state": state,
            "match": match,
            "source_covered": source_covered,
            "action_allowed": state == "active"
            and health["status"] == "current"
            and source_covered
            and match["identity_matched"],
            "action": action["action"] if action else None,
        }
        (history if state == "superseded" else assessments).append(item)
    notices = [
        item
        for item in assessments
        if item["action_allowed"]
        and item["match"]["classification"] in ("affected", "possible")
    ]
    return {
        "equipment": {"id": equipment["id"], **context},
        "context_digest": context_digest,
        "catalog": {**health, "published_count": len(latest)},
        "assessments": assessments,
        "notices": notices,
        "history": history,
        "history_truncated": history_truncated,
        "past_actions": [
            action
            for action in actions
            if action["context_digest"] != context_digest
            or action["revision"] != latest.get(action["advisory_id"])
        ],
    }


def validate_owner_action(payload, expected_context):
    payload = _object(
        payload,
        ("context_digest", "acknowledged", "remedy_status", "evidence", "private_note"),
        "action",
    )
    if payload.get("context_digest") != expected_context:
        raise AdvisoryError("equipment_context_changed", 409)
    acknowledged = payload.get("acknowledged", False)
    if not isinstance(acknowledged, bool):
        raise AdvisoryError(field="acknowledged")
    status = _choice(
        payload.get("remedy_status", "unknown"), REMEDY_STATUSES, "remedy_status"
    )
    evidence = _object(
        payload.get("evidence", {}), ("kind", "reviewed_on", "source_url"), "evidence"
    )
    kind = _choice(evidence.get("kind", "unknown"), SOURCE_KINDS, "evidence.kind")
    reviewed = _date(evidence.get("reviewed_on"), "evidence.reviewed_on", optional=True)
    url = _url(evidence.get("source_url"), "evidence.source_url", optional=True)
    if (
        (kind == "unknown" and (reviewed or url))
        or (kind != "unknown" and not reviewed)
        or (status == "reported_done" and kind == "unknown")
    ):
        raise AdvisoryError(field="evidence")
    return {
        "acknowledged": acknowledged,
        "remedy_status": status,
        "evidence": {"kind": kind, "reviewed_on": reviewed, "source_url": url},
        "private_note": _text(
            payload.get("private_note"), 1000, "private_note", optional=True
        ),
    }
