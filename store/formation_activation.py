# SPDX-License-Identifier: AGPL-3.0-or-later
"""Activate a LEG only after an administrator records actual VNB start evidence.

The incoming correspondence remains the source record. Community fields hold
its fingerprint, the declared start date and the actors for the two explicit
transitions. Transport acknowledgements never enter this path automatically.
"""

import hashlib
from datetime import UTC, date

from psycopg2.extras import Json

import community_access
from translation_markers import gettext_noop as _N


class ActivationDenied(Exception):
    """The actor is not a confirmed community administrator."""


class ActivationConflict(Exception):
    """The community or its source evidence cannot support the transition."""


def _get_connection():
    import database

    return database.get_connection()


def _locked_community(cur, community_id, actor_building_id):
    cur.execute(
        "SELECT * FROM communities WHERE community_id = %s FOR UPDATE",
        (community_id,),
    )
    community = cur.fetchone()
    cur.execute(
        """SELECT role, access_roles, status FROM community_members
           WHERE community_id = %s AND building_id = %s FOR SHARE""",
        (community_id, actor_building_id),
    )
    actor = cur.fetchone()
    if (
        not community
        or not actor
        or actor["status"] != "confirmed"
        or not community_access.is_administrator(actor)
    ):
        raise ActivationDenied()
    return community


def _evidence_hash(cur, community_id, entry_id):
    cur.execute(
        """SELECT attachment_data FROM correspondence_log
           WHERE id = %s AND community_id = %s AND direction = 'in'
           FOR SHARE""",
        (entry_id, community_id),
    )
    entry = cur.fetchone()
    if not entry or not entry["attachment_data"]:
        raise ActivationConflict(
            _N(
                "Wählen Sie einen eingehenden Beleg mit Anhang aus dem Korrespondenzjournal."
            )
        )
    return hashlib.sha256(bytes(entry["attachment_data"])).hexdigest()


def record_vnb_start_confirmation(
    community_id: str,
    actor_building_id: str,
    correspondence_id: str,
    effective_date: str,
    reference: str,
) -> None:
    """Bind one incoming source to a submitted LEG; identical retries are safe."""
    try:
        entry_id = int(correspondence_id)
        start = date.fromisoformat(effective_date)
        if entry_id < 1 or start.isoformat() != effective_date:
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError(
            _N("Geben Sie einen gültigen Beleg und ein Startdatum an.")
        ) from None
    reference = reference.strip()
    if not reference or len(reference) > 255 or any(ord(c) < 32 for c in reference):
        raise ValueError(_N("Geben Sie eine Referenz mit höchstens 255 Zeichen an."))
    with _get_connection() as conn, conn.cursor() as cur:
        community = _locked_community(cur, community_id, actor_building_id)
        digest = _evidence_hash(cur, community_id, entry_id)
        source = {
            "version": "vnb-start/1",
            "correspondence_id": entry_id,
            "effective_date": start.isoformat(),
            "reference": reference,
            "evidence_sha256": digest,
        }
        previous = community["vnb_start_confirmation"]
        if previous:
            if isinstance(previous, dict) and all(
                previous.get(key) == value for key, value in source.items()
            ):
                return
            raise ActivationConflict(
                _N("Eine andere Startbestätigung liegt bereits vor.")
            )
        if community["status"] != "dso_submitted":
            raise ActivationConflict(
                _N("Melden Sie die LEG zuerst beim Netzbetreiber an.")
            )
        cur.execute("SELECT CURRENT_TIMESTAMP AS now")
        source.update(
            recorded_by=actor_building_id,
            recorded_at=cur.fetchone()["now"].astimezone(UTC).isoformat(),
        )
        cur.execute(
            """UPDATE communities SET status = 'dso_approved',
               dso_approved_at = CURRENT_TIMESTAMP AT TIME ZONE 'UTC',
               vnb_start_confirmation = %s, updated_at = CURRENT_TIMESTAMP
               WHERE community_id = %s""",
            (Json(source), community_id),
        )


def activate_community(community_id: str, actor_building_id: str) -> None:
    """Activate explicitly on or after the VNB-confirmed local start date."""
    with _get_connection() as conn, conn.cursor() as cur:
        community = _locked_community(cur, community_id, actor_building_id)
        proof = community["vnb_start_confirmation"]
        proof = proof if isinstance(proof, dict) else {}
        try:
            start = date.fromisoformat(proof["effective_date"])
            entry_id = int(proof["correspondence_id"])
            valid = (
                proof["version"] == "vnb-start/1"
                and bool(proof["reference"])
                and bool(proof["recorded_by"])
                and bool(proof["recorded_at"])
                and isinstance(proof["evidence_sha256"], str)
                and len(proof["evidence_sha256"]) == 64
                and community["dso_approved_at"] is not None
            )
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid or community["status"] not in {"dso_approved", "active"}:
            raise ActivationConflict(
                _N("Erfassen Sie zuerst die Startbestätigung des Netzbetreibers.")
            )
        if _evidence_hash(cur, community_id, entry_id) != proof["evidence_sha256"]:
            raise ActivationConflict(
                _N("Der Beleg stimmt nicht mehr mit der Startbestätigung überein.")
            )
        cur.execute(
            "SELECT (CURRENT_TIMESTAMP AT TIME ZONE 'Europe/Zurich')::date AS today"
        )
        if start > cur.fetchone()["today"]:
            raise ActivationConflict(
                _N("Das bestätigte Startdatum liegt noch in der Zukunft.")
            )
        if community["status"] == "active":
            return
        cur.execute("SELECT CURRENT_TIMESTAMP AS now")
        proof.update(
            activated_by=actor_building_id,
            activated_at=cur.fetchone()["now"].astimezone(UTC).isoformat(),
        )
        cur.execute(
            """UPDATE communities SET status = 'active',
               activated_at = CURRENT_TIMESTAMP AT TIME ZONE 'UTC',
               vnb_start_confirmation = %s, updated_at = CURRENT_TIMESTAMP
               WHERE community_id = %s""",
            (Json(proof), community_id),
        )
