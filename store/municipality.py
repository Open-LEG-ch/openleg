# SPDX-License-Identifier: AGPL-3.0-or-later
"""Municipality lookup repository.

Repository module for municipality lookup by id, bfs_number, subdomain, or
admin email. The connection seam is resolved via ``database.get_connection`` at
call time so existing tests that ``monkeypatch.setattr(database, "get_connection", ...)``
keep working unchanged and ``database`` can re-export these functions for
legacy callers.
"""

import logging

logger = logging.getLogger(__name__)


def _get_connection():
    import database

    return database.get_connection()


def get_municipality(bfs_number=None, subdomain=None, municipality_id=None):
    """Get a municipality by BFS number, subdomain, or database id."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            if municipality_id:
                cur.execute(
                    "SELECT * FROM municipalities WHERE id = %s",
                    (municipality_id,),
                )
            elif bfs_number:
                cur.execute(
                    "SELECT * FROM municipalities WHERE bfs_number = %s",
                    (bfs_number,),
                )
            elif subdomain:
                cur.execute(
                    "SELECT * FROM municipalities WHERE subdomain = %s",
                    (subdomain,),
                )
            else:
                return None
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"[DB] Error getting municipality: {e}")
        return None


def get_municipality_by_admin_email(email: str) -> dict | None:
    """Find a municipality by its registered admin email address."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM municipalities WHERE LOWER(admin_email) = LOWER(%s)",
                (email,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"[DB] Error getting municipality by admin email: {e}")
        return None
