# SPDX-License-Identifier: AGPL-3.0-or-later
"""LEG document repository.

Owns the generated formation documents and their signing status.
"""

import logging

logger = logging.getLogger(__name__)


def _get_connection():
    import database

    return database.get_connection()


def update_document_signing_status(deepsign_document_id: str, status: str) -> bool:
    """Update LEG document signing status from DeepSign webhook."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE leg_documents SET signing_status = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE deepsign_document_id = %s
                """,
                    (status, deepsign_document_id),
                )
                return cur.rowcount > 0
    except Exception as e:
        logger.error(f"[DB] Error updating document signing status: {e}")
        return False


def store_leg_document(
    community_id: str, doc_type: str, pdf_bytes: bytes, filename: str
) -> int:
    """Store generated LEG document PDF."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO leg_documents (community_id, doc_type, filename, pdf_data)
                    VALUES (%s, %s, %s, %s) RETURNING id
                """,
                    (community_id, doc_type, filename, pdf_bytes),
                )
                return dict(cur.fetchone())["id"]
    except Exception as e:
        logger.error(f"[DB] Error storing leg document: {e}")
        return 0


def get_leg_document(doc_id: int) -> dict | None:
    """Get one stored LEG document including its PDF bytes."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT id, community_id, doc_type, filename, pdf_data,
                           signing_status, created_at
                    FROM leg_documents WHERE id = %s
                """,
                (doc_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"[DB] Error getting leg document: {e}")
        return None


def list_leg_documents(community_id: str) -> list[dict]:
    """List all documents for a community."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, doc_type, filename, signing_status, deepsign_document_id, created_at
                    FROM leg_documents WHERE community_id = %s ORDER BY created_at DESC
                """,
                    (community_id,),
                )
                return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error listing leg documents: {e}")
        return []
