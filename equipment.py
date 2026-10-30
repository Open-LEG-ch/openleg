# SPDX-License-Identifier: AGPL-3.0-or-later
"""Owner-declared equipment facts. No device discovery, control or safety score."""

import ipaddress
import re
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from translation_markers import gettext_noop as _N

MAX_BYTES = 24_576
MAX_RECORDS = 100
SOURCES = {
    "unknown": _N("Unbekannt"),
    "owner_report": _N("Eigene Angabe"),
    "device_observation": _N("Am Gerät abgelesen"),
    "installer_evidence": _N("Hinterlegter Installateurnachweis"),
}
CHECKS = {
    "default_passwords": (
        _N("Standardpasswörter"),
        _N("Gerät und Logger anhand der Anleitung prüfen."),
    ),
    "account_mfa": (
        _N("Mehrfaktor-Anmeldung"),
        _N(
            "Für dieses Modell und das zugehörige Cloud-Konto klären, ob MFA unterstützt wird."
        ),
    ),
    "obsolete_access": (
        _N("Nicht mehr benötigte Zugänge"),
        _N("Benannte Fernzugänge und frühere Installateurkonten prüfen."),
    ),
    "update_support": (
        _N("Updates und Support"),
        _N("Firmwarestand und Supportende anhand der Herstellerangaben prüfen."),
    ),
    "exposed_services": (
        _N("Internet-Erreichbarkeit"),
        _N("Öffentliche Dienste und Fernzugriff mit dem Betreiber klären."),
    ),
    "network_separation": (
        _N("Netztrennung"),
        _N("Trennung vom privaten Netz mit dem Betreiber prüfen."),
    ),
}
APPLICABILITY = {
    "unknown": _N("Unbekannt"),
    "applicable": _N("Für dieses Modell anwendbar"),
    "not_supported": _N("Vom Modell nicht unterstützt"),
    "not_applicable": _N("Nicht anwendbar"),
}
STATUSES = {
    "unknown": _N("Unbekannt"),
    "open": _N("Offen"),
    "done": _N("Erledigt"),
    "needs_installer": _N("Installateur beauftragen"),
}
CLOUD_DEPENDENCY = {
    "unknown": _N("Unbekannt"),
    "required": _N("Cloud erforderlich"),
    "optional": _N("Cloud optional"),
    "none": _N("Keine Cloud-Abhängigkeit angegeben"),
}
INSTALLER_ACCESS = {
    "unknown": _N("Unbekannt"),
    "active": _N("Zugang vorhanden"),
    "removed": _N("Zugang entfernt"),
    "none": _N("Kein Zugang angegeben"),
}
CLOUD_REVIEW = {
    "unknown": _N("Unbekannt"),
    "not_reviewed": _N("Noch nicht geprüft"),
    "reviewed": _N("Manuell geprüft"),
}
REMOTE_ACCESS = {
    "unknown": _N("Unbekannt"),
    "possible": _N("Fernzugriff möglich"),
    "disabled": _N("Als deaktiviert angegeben"),
    "none": _N("Kein Fernzugriff angegeben"),
}
BACKUP = {
    "unknown": _N("Unbekannt"),
    "none": _N("Kein Ersatzstrom"),
    "partial": _N("Ersatzstrom für einzelne Stromkreise angegeben"),
    "available": _N("Ersatzstrom angegeben"),
    "requires_modification": _N("Umbau erforderlich"),
}
TEXT_FIELDS = {
    "manufacturer": 120,
    "model": 120,
    "firmware": 120,
    "logger": 120,
    "serial_number": 120,
    "network_details": 2000,
    "private_notes": 2000,
    "installer_users": 1000,
}
SOURCE_LINKS = (
    (_N("BACS: Gerätesicherheit"), "https://www.bacs.admin.ch/en/device-security"),
    (
        _N("Swissolar: Solarstrom bei Netzausfall"),
        "https://www.swissolar.ch/de/wissen/anlagenbetrieb/solarstrom-bei-netzausfall",
    ),
)


class EquipmentError(ValueError):
    def __init__(self, code="invalid_record", status=400, field=None):
        self.code = code
        self.status = status
        self.field = field
        super().__init__(code)


def _object(value, fields, field):
    if not isinstance(value, dict) or set(value) - set(fields):
        raise EquipmentError(field=field)
    return value


def _choice(value, options, field):
    if not isinstance(value, str) or value not in options:
        raise EquipmentError(field=field)
    return value


def _text(value, limit, field):
    if value is None or value == "":
        return None
    if (
        not isinstance(value, str)
        or len(value) > limit
        or any(ord(char) < 32 and char not in "\n\t" for char in value)
    ):
        raise EquipmentError(field=field)
    return value.strip() or None


def _power(value, field):
    if value is None or value == "":
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float, str))
        or len(str(value)) > 32
    ):
        raise EquipmentError(field=field)
    try:
        number = Decimal(str(value))
        if (
            not number.is_finite()
            or not 0 < number <= 100_000
            or number.as_tuple().exponent < -3
        ):
            raise EquipmentError(field=field)
    except InvalidOperation:
        raise EquipmentError(field=field) from None
    return format(number.normalize(), "f")


def validate_public_source_url(value, field="source_url"):
    """Validate a public HTTPS documentation link without fetching it."""
    value = _text(value, 500, field)
    if value is None:
        return None
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        invalid = (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.port not in (None, 443)
            or len(host) > 253
            or any(
                not re.fullmatch(
                    r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label
                )
                for label in host.split(".")
            )
            or not re.fullmatch(
                r"(?:[a-zA-Z]{2,63}|xn--[a-zA-Z0-9-]{2,59})", host.rsplit(".", 1)[-1]
            )
            or "." not in host
            or host.lower() == "home.arpa"
            or host.lower().endswith(
                (
                    ".local",
                    ".localhost",
                    ".internal",
                    ".test",
                    ".invalid",
                    ".example",
                    ".home.arpa",
                    ".lan",
                    ".home",
                    ".corp",
                )
            )
            or any(char.isspace() for char in value)
            or "\\" in value
        )
        try:
            ipaddress.ip_address(host)
            invalid = True
        except ValueError:
            pass
        if invalid:
            raise ValueError
    except ValueError:
        raise EquipmentError(field=field) from None
    return value


def _evidence(payload, field):
    payload = _object(payload, ("kind", "reviewed_on", "source_url"), field)
    kind = _choice(payload.get("kind", "unknown"), SOURCES, field + ".kind")
    reviewed = payload.get("reviewed_on")
    if reviewed == "":
        reviewed = None
    if reviewed is not None:
        try:
            if not isinstance(reviewed, str) or not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}", reviewed
            ):
                raise ValueError
            parsed = date.fromisoformat(reviewed)
            if parsed > datetime.now(UTC).date():
                raise ValueError
        except ValueError:
            raise EquipmentError(field=field + ".reviewed_on") from None
    url = validate_public_source_url(payload.get("source_url"), field + ".source_url")
    if (kind == "unknown" and (reviewed or url)) or (
        kind != "unknown" and not reviewed
    ):
        raise EquipmentError(field=field + ".reviewed_on")
    return {"kind": kind, "reviewed_on": reviewed, "source_url": url}


def validate_record(payload):
    payload = _object(
        payload,
        (
            *TEXT_FIELDS,
            "rated_ac_kw",
            "pv_dc_kwp",
            "cloud_dependency",
            "installer_access",
            "evidence",
            "checklist",
            "vendor_cloud_review",
            "backup",
        ),
        "record",
    )
    record = {
        field: _text(payload.get(field), limit, field)
        for field, limit in TEXT_FIELDS.items()
    }
    for field in ("rated_ac_kw", "pv_dc_kwp"):
        record[field] = _power(payload.get(field), field)
    record["cloud_dependency"] = _choice(
        payload.get("cloud_dependency", "unknown"), CLOUD_DEPENDENCY, "cloud_dependency"
    )
    record["installer_access"] = _choice(
        payload.get("installer_access", "unknown"), INSTALLER_ACCESS, "installer_access"
    )
    record["evidence"] = _evidence(payload.get("evidence", {}), "evidence")
    checklist = _object(payload.get("checklist", {}), CHECKS, "checklist")
    record["checklist"] = {}
    for key in CHECKS:
        field = "checklist." + key
        item = _object(
            checklist.get(key, {}), ("status", "applicability", "evidence"), field
        )
        status = _choice(item.get("status", "unknown"), STATUSES, field + ".status")
        applicability = _choice(
            item.get("applicability", "unknown"),
            APPLICABILITY,
            field + ".applicability",
        )
        evidence = _evidence(item.get("evidence", {}), field + ".evidence")
        if (
            (status != "unknown" or applicability != "unknown")
            and evidence["kind"] == "unknown"
        ) or (status == "done" and applicability != "applicable"):
            raise EquipmentError(field=field + ".evidence")
        if applicability != "unknown" and (
            not record["manufacturer"] or not record["model"]
        ):
            raise EquipmentError(field=field + ".applicability")
        record["checklist"][key] = {
            "status": status,
            "applicability": applicability,
            "evidence": evidence,
        }
    cloud = _object(
        payload.get("vendor_cloud_review", {}),
        ("status", "remote_access", "evidence"),
        "vendor_cloud_review",
    )
    cloud_status = _choice(
        cloud.get("status", "unknown"), CLOUD_REVIEW, "vendor_cloud_review.status"
    )
    remote = _choice(
        cloud.get("remote_access", "unknown"),
        REMOTE_ACCESS,
        "vendor_cloud_review.remote_access",
    )
    cloud_evidence = _evidence(
        cloud.get("evidence", {}), "vendor_cloud_review.evidence"
    )
    if (cloud_status == "reviewed" and cloud_evidence["kind"] == "unknown") or (
        cloud_status != "reviewed"
        and (remote != "unknown" or cloud_evidence["kind"] != "unknown")
    ):
        raise EquipmentError(field="vendor_cloud_review.evidence")
    record["vendor_cloud_review"] = {
        "status": cloud_status,
        "remote_access": remote,
        "evidence": cloud_evidence,
    }
    backup = _object(
        payload.get("backup", {}), ("capability", "installer_evidence"), "backup"
    )
    installer_evidence = _evidence(
        backup.get("installer_evidence", {}), "backup.installer_evidence"
    )
    if installer_evidence["kind"] not in ("unknown", "installer_evidence"):
        raise EquipmentError(field="backup.installer_evidence.kind")
    record["backup"] = {
        "capability": _choice(
            backup.get("capability", "unknown"), BACKUP, "backup.capability"
        ),
        "installer_evidence": installer_evidence,
    }
    return record


def reset_model_evidence(previous, record):
    """Invalidate model-specific assertions when their device context changes."""
    context = ("manufacturer", "model", "firmware")
    if any(previous[key] for key in context) and any(
        previous[key] != record[key] for key in context
    ):
        unknown = validate_record({})
        return {
            **record,
            **{
                key: unknown[key]
                for key in ("checklist", "vendor_cloud_review", "backup")
            },
        }
    return record


def installer_handoff(record):
    """Allowlist the download; private annotations never travel to an installer."""
    return {
        "schema_version": "equipment-handoff/1",
        "notice": _N(
            "Eigene Angaben, Gerätebeobachtungen und hinterlegte Installateurnachweise. OpenLEG prüft diese Angaben nicht. Änderungen nur durch zuständige Fachpersonen beauftragen."
        ),
        "equipment": {
            key: record[key]
            for key in (
                "manufacturer",
                "model",
                "firmware",
                "logger",
                "rated_ac_kw",
                "pv_dc_kwp",
                "cloud_dependency",
                "installer_access",
                "evidence",
            )
        },
        "findings": [
            {"check": key, "label": CHECKS[key][0], **item}
            for key, item in record["checklist"].items()
        ],
        "vendor_cloud_review": record["vendor_cloud_review"],
        "backup": record["backup"],
        "sources": [{"label": label, "url": url} for label, url in SOURCE_LINKS],
    }
