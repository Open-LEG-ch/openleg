# SPDX-License-Identifier: AGPL-3.0-or-later
"""Collect participant receipts for an exact, reviewed formation bundle."""

import hashlib
import json

from translation_markers import gettext_noop as _N


class SignatureDenied(PermissionError):
    """The signer is not a confirmed, consenting participant."""


class SignatureConflict(ValueError):
    """The current formation bundle cannot accept this receipt."""


def _get_connection():
    import database

    return database.get_connection()


def participant_manifest(cursor, community_id):
    """Lock membership and profile facts used by one formation document bundle."""
    cursor.execute(
        """SELECT building_id FROM community_members WHERE community_id = %s
           ORDER BY building_id FOR SHARE""",
        (community_id,),
    )
    cursor.fetchall()
    cursor.execute(
        """SELECT cm.building_id
           FROM community_members cm
           JOIN buildings b ON b.building_id = cm.building_id
           JOIN consents c ON c.building_id = cm.building_id
           WHERE cm.community_id = %s
           ORDER BY cm.building_id FOR SHARE OF b, c""",
        (community_id,),
    )
    cursor.fetchall()
    cursor.execute(
        """SELECT cm.building_id, b.email, b.address, b.potential_pv_kwp
           FROM community_members cm
           JOIN buildings b ON b.building_id = cm.building_id
           JOIN consents c ON c.building_id = cm.building_id
           WHERE cm.community_id = %s AND cm.status = 'confirmed'
             AND b.verified = TRUE AND c.share_with_neighbors = TRUE
           ORDER BY cm.building_id""",
        (community_id,),
    )
    return [
        {
            "building_id": row["building_id"],
            "name": row["email"] or row["building_id"],
            "address": row["address"] or "",
            "role": "producer"
            if row["potential_pv_kwp"] and float(row["potential_pv_kwp"]) > 0
            else "consumer",
        }
        for row in cursor.fetchall()
    ]


def bundle_fingerprint(documents, signers):
    """Identify exact stored PDF bytes and the ordered original signers."""
    facts = {
        "documents": [
            {
                "id": item["id"],
                "sha256": hashlib.sha256(bytes(item["pdf_data"])).hexdigest(),
            }
            for item in documents
        ],
        "signers": signers,
    }
    return hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()


def locked_signature_bundle(cursor, community_id, actor):
    """Validate the current bundle while holding its community and fact locks."""
    cursor.execute(
        "SELECT status FROM communities WHERE community_id = %s FOR UPDATE",
        (community_id,),
    )
    community = cursor.fetchone()
    expected_manifest = participant_manifest(cursor, community_id)
    signers = [row["building_id"] for row in expected_manifest]
    if not community or actor not in signers:
        raise SignatureDenied()
    cursor.execute(
        """SELECT id, doc_type, filename, pdf_data, participant_manifest FROM leg_documents
           WHERE community_id = %s AND signing_status IS DISTINCT FROM 'superseded'
           ORDER BY id FOR UPDATE""",
        (community_id,),
    )
    documents = cursor.fetchall()
    from formation_wizard import FORMATION_CONFIG

    if (
        community["status"] not in {"documents_generated", "signatures_pending"}
        or len(signers) < FORMATION_CONFIG["min_community_size"]
        or not documents
        or any(not item["pdf_data"] for item in documents)
    ):
        raise SignatureConflict(
            _N(
                "Erstellen Sie zuerst die vollständigen Vertragsentwürfe für mindestens drei bestätigte Haushalte."
            )
        )
    if (
        len(documents) != len(signers) + 1
        or sum(item["doc_type"] == "gemeinschaftsvereinbarung" for item in documents)
        != 1
        or any(
            not isinstance(item["participant_manifest"], list)
            or sorted(
                item["participant_manifest"], key=lambda person: person["building_id"]
            )
            != expected_manifest
            for item in documents
        )
    ):
        raise SignatureConflict(
            _N(
                "Die Vertragsentwürfe enthalten nicht alle aktuellen Teilnehmenden. Erstellen Sie die Entwürfe erneut."
            )
        )
    fingerprint = bundle_fingerprint(documents, signers)
    if community["status"] == "signatures_pending":
        cursor.execute(
            """SELECT building_id FROM leg_signature_receipts
               WHERE community_id = %s AND bundle_fingerprint = %s""",
            (community_id, fingerprint),
        )
        if {row["building_id"] for row in cursor.fetchall()} != set(signers):
            raise SignatureConflict(
                _N(
                    "Die Unterzeichnung passt nicht mehr zu den aktuellen Dokumenten und Teilnehmenden."
                )
            )
    return fingerprint, signers


def formation_signature_bundle(community_id, actor):
    """Read the exact document and participant fingerprint awaiting signatures."""
    with _get_connection() as connection, connection.cursor() as cursor:
        fingerprint, signers = locked_signature_bundle(cursor, community_id, actor)
        cursor.execute(
            """SELECT building_id FROM leg_signature_receipts
               WHERE community_id = %s AND bundle_fingerprint = %s
               ORDER BY building_id""",
            (community_id, fingerprint),
        )
        return {
            "fingerprint": fingerprint,
            "required": signers,
            "signed": [row["building_id"] for row in cursor.fetchall()],
        }


def record_formation_signature(community_id, actor, fingerprint, provider, evidence):
    """Persist one provider receipt; advance only after every current signer."""
    if not provider or len(provider) > 64 or not evidence or len(evidence) > 65536:
        raise SignatureConflict(_N("Der Unterschriftsbeleg ist ungültig."))
    with _get_connection() as connection, connection.cursor() as cursor:
        current, signers = locked_signature_bundle(cursor, community_id, actor)
        if fingerprint != current:
            raise SignatureConflict(
                _N(
                    "Die Dokumente oder Teilnehmenden wurden geändert. Prüfen Sie den aktuellen Entwurf."
                )
            )
        cursor.execute(
            """INSERT INTO leg_signature_receipts
                   (community_id, bundle_fingerprint, building_id, provider, evidence)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (community_id, bundle_fingerprint, building_id) DO NOTHING""",
            (community_id, current, actor, provider, evidence),
        )
        if cursor.rowcount:
            from store.operator_api import enqueue_event

            enqueue_event(
                cursor,
                "formation.signature.recorded",
                current + ":" + actor,
                community_id,
                {"building_id": actor, "bundle": current, "provider": provider},
            )
        cursor.execute(
            """SELECT building_id FROM leg_signature_receipts
               WHERE community_id = %s AND bundle_fingerprint = %s""",
            (community_id, current),
        )
        if {row["building_id"] for row in cursor.fetchall()} == set(signers):
            cursor.execute(
                """UPDATE leg_documents SET signing_status = 'completed',
                          updated_at = CURRENT_TIMESTAMP
                   WHERE community_id = %s
                     AND signing_status NOT IN ('completed', 'superseded')""",
                (community_id,),
            )
            cursor.execute(
                """UPDATE communities SET status = 'signatures_pending',
                          updated_at = CURRENT_TIMESTAMP
                   WHERE community_id = %s AND status = 'documents_generated'""",
                (community_id,),
            )


def validate_formation_signature_receipts(community_id, actor):
    """Preserve legacy provider bundles; reject retired participant receipts."""
    with _get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM leg_signature_receipts WHERE community_id = %s LIMIT 1",
            (community_id,),
        )
        if cursor.fetchone():
            locked_signature_bundle(cursor, community_id, actor)
