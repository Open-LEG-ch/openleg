# SPDX-License-Identifier: AGPL-3.0-or-later
"""Atomic persistence for a LEG's generated document bundle."""

from psycopg2.extras import Json

from store.formation_signatures import bundle_fingerprint, participant_manifest


def _manifest(document):
    """Normalize rendered facts while refusing unbound legacy documents."""
    manifest = document.get("participant_manifest")
    if (
        not isinstance(manifest, list)
        or not manifest
        or any(
            not isinstance(person, dict)
            or not isinstance(person.get("building_id"), str)
            for person in manifest
        )
    ):
        raise ValueError("Signierte Dokumente können nicht ersetzt werden.")
    return sorted(manifest, key=lambda person: person["building_id"])


def _supersede_changed_local_bundle(cursor, community_id, documents):
    """Retire a stale local receipt bundle, preserving its signed PDF history."""
    cursor.execute(
        "SELECT status FROM communities WHERE community_id = %s", (community_id,)
    )
    community = cursor.fetchone()
    cursor.execute(
        "SELECT 1 FROM vnb_submission_cases WHERE community_id = %s LIMIT 1",
        (community_id,),
    )
    if (
        not community
        or community["status"] != "signatures_pending"
        or cursor.fetchone()
    ):
        raise ValueError("Signierte Dokumente können nicht ersetzt werden.")
    cursor.execute(
        """SELECT id, doc_type, pdf_data, participant_manifest, signing_status,
                  deepsign_document_id, signed_pdf_url FROM leg_documents
           WHERE community_id = %s AND signing_status IS DISTINCT FROM 'superseded'
           ORDER BY id FOR UPDATE""",
        (community_id,),
    )
    previous = cursor.fetchall()
    if not previous or any(
        item["signing_status"] != "completed"
        or item["deepsign_document_id"]
        or item["signed_pdf_url"]
        or not item["pdf_data"]
        for item in previous
    ):
        raise ValueError("Signierte Dokumente können nicht ersetzt werden.")
    original = _manifest(previous[0])
    current = participant_manifest(cursor, community_id)
    if (
        original == current
        or len(previous) != len(original) + 1
        or len(documents) != len(current) + 1
        or sum(item["doc_type"] == "gemeinschaftsvereinbarung" for item in previous)
        != 1
        or sum(item["doc_type"] == "gemeinschaftsvereinbarung" for item in documents)
        != 1
        or sum(item["doc_type"] == "teilnehmervertrag" for item in documents)
        != len(current)
        or any(_manifest(item) != original for item in previous)
        or any(_manifest(item) != current for item in documents)
    ):
        raise ValueError("Signierte Dokumente können nicht ersetzt werden.")
    signers = [person["building_id"] for person in original]
    fingerprint = bundle_fingerprint(previous, signers)
    cursor.execute(
        """SELECT building_id, provider FROM leg_signature_receipts
           WHERE community_id = %s AND bundle_fingerprint = %s FOR SHARE""",
        (community_id, fingerprint),
    )
    receipts = cursor.fetchall()
    if {row["building_id"] for row in receipts} != set(signers) or any(
        row["provider"] != "sonnwil-simulation" for row in receipts
    ):
        raise ValueError("Signierte Dokumente können nicht ersetzt werden.")
    cursor.execute(
        """UPDATE leg_documents SET signing_status = 'superseded',
                  updated_at = CURRENT_TIMESTAMP
           WHERE community_id = %s AND signing_status = 'completed'""",
        (community_id,),
    )


def _get_connection():
    import database

    return database.get_connection()


def replace_leg_document_bundle(community_id: str, documents: list[dict]) -> int:
    """Replace drafts or a stale local bundle atomically; retain signed history."""
    if not documents:
        raise ValueError("Der Dokumentensatz ist leer.")

    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT community_id FROM communities WHERE community_id = %s FOR UPDATE",
            (community_id,),
        )
        cur.execute(
            """
                SELECT EXISTS (
                    SELECT 1 FROM leg_documents
                    WHERE community_id = %s AND signing_status NOT IN ('unsigned', 'superseded')
                ) AS has_signed
                """,
            (community_id,),
        )
        if dict(cur.fetchone())["has_signed"]:
            _supersede_changed_local_bundle(cur, community_id, documents)

        cur.execute(
            """
                DELETE FROM leg_documents
                WHERE community_id = %s AND signing_status = 'unsigned'
                """,
            (community_id,),
        )
        for document in documents:
            cur.execute(
                """
                    INSERT INTO leg_documents (
                        community_id, doc_type, filename, pdf_data, participant_manifest
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                (
                    community_id,
                    document["doc_type"],
                    document["filename"],
                    document["pdf_data"],
                    Json(document.get("participant_manifest")),
                ),
            )

        cur.execute(
            """
                UPDATE communities
                SET status = 'documents_generated', updated_at = CURRENT_TIMESTAMP
                WHERE community_id = %s
                """,
            (community_id,),
        )
    return len(documents)
