# SPDX-License-Identifier: AGPL-3.0-or-later
"""Atomic persistence for a LEG's generated document bundle."""


def _get_connection():
    import database

    return database.get_connection()


def replace_leg_document_bundle(community_id: str, documents: list[dict]) -> int:
    """Replace unsigned drafts and advance formation state in one transaction."""
    if not documents:
        raise ValueError("Der Dokumentensatz ist leer.")

    with _get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT community_id FROM communities WHERE community_id = %s FOR UPDATE",
                (community_id,),
            )
            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM leg_documents
                    WHERE community_id = %s AND signing_status <> 'unsigned'
                ) AS has_signed
                """,
                (community_id,),
            )
            if dict(cur.fetchone())["has_signed"]:
                raise ValueError("Signierte Dokumente können nicht ersetzt werden.")

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
                        community_id, doc_type, filename, pdf_data
                    ) VALUES (%s, %s, %s, %s)
                    """,
                    (
                        community_id,
                        document["doc_type"],
                        document["filename"],
                        document["pdf_data"],
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
