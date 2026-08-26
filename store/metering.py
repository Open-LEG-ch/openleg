# SPDX-License-Identifier: AGPL-3.0-or-later
"""SDAT metering point persistence repository.

Holds the VNB metering point registry, the 15-minute E66 series and the
per-file import ledger. Resolves the connection seam via
``database.get_connection`` at call time so monkeypatches keep working and
``database`` can re-export these functions.

Daily E66 deliveries cover a five day window, so consecutive files overlap by
four days. Writes are therefore idempotent upserts that skip rows whose values
did not change and report how many rows were new versus corrected.
"""

import logging

logger = logging.getLogger(__name__)

_NUMERIC_FIELDS = ("total_kwh", "grid_kwh", "community_kwh")

_READING_COLUMNS = (
    "metering_point_id",
    "direction",
    "measured_at",
    "resolution_minutes",
    "total_kwh",
    "grid_kwh",
    "community_kwh",
    "condition_code",
    "source_document_id",
)

_POINT_STUB_SQL = """
    INSERT INTO metering_points (metering_point_id) VALUES %s
    ON CONFLICT (metering_point_id) DO NOTHING
"""

# The WHERE guard does double duty: it keeps the ~80 percent of rows that an
# overlapping delivery repeats verbatim from churning the table, and it makes
# RETURNING report exactly the rows that moved. xmax = 0 marks a fresh insert,
# so anything else returned is a correction of an earlier delivery.
_READING_UPSERT_SQL = f"""
    INSERT INTO metering_point_readings ({", ".join(_READING_COLUMNS)})
    VALUES %s
    ON CONFLICT (metering_point_id, direction, measured_at) DO UPDATE SET
        total_kwh = EXCLUDED.total_kwh,
        grid_kwh = EXCLUDED.grid_kwh,
        community_kwh = EXCLUDED.community_kwh,
        condition_code = EXCLUDED.condition_code,
        resolution_minutes = EXCLUDED.resolution_minutes,
        source_document_id = EXCLUDED.source_document_id,
        imported_at = NOW()
    WHERE metering_point_readings.total_kwh
              IS DISTINCT FROM EXCLUDED.total_kwh
       OR metering_point_readings.grid_kwh
              IS DISTINCT FROM EXCLUDED.grid_kwh
       OR metering_point_readings.community_kwh
              IS DISTINCT FROM EXCLUDED.community_kwh
       OR metering_point_readings.condition_code
              IS DISTINCT FROM EXCLUDED.condition_code
       OR metering_point_readings.resolution_minutes
              IS DISTINCT FROM EXCLUDED.resolution_minutes
       OR metering_point_readings.source_document_id
              IS DISTINCT FROM EXCLUDED.source_document_id
    RETURNING metering_point_id, direction, measured_at, (xmax = 0) AS inserted
"""

_POINT_UPSERT_SQL = """
    INSERT INTO metering_points
        (metering_point_id, vnb_community_id, community_id, building_id,
         alias, address)
    VALUES %s
    ON CONFLICT (metering_point_id) DO UPDATE SET
        vnb_community_id = COALESCE(
            EXCLUDED.vnb_community_id, metering_points.vnb_community_id),
        community_id = COALESCE(
            EXCLUDED.community_id, metering_points.community_id),
        building_id = COALESCE(
            EXCLUDED.building_id, metering_points.building_id),
        alias = COALESCE(EXCLUDED.alias, metering_points.alias),
        address = COALESCE(EXCLUDED.address, metering_points.address),
        updated_at = NOW()
"""


def _get_connection():
    import database

    return database.get_connection()


def _floatify(row):
    """NUMERIC kommt als Decimal zurück; pandas und numpy brauchen float."""
    result = dict(row)
    for field in _NUMERIC_FIELDS:
        if result.get(field) is not None:
            result[field] = float(result[field])
    return result


def _dedupe(rows):
    """Auf (Messpunkt, Richtung, Zeit) entdoppeln, letzter Eintrag gewinnt.

    Derselbe Konfliktschlüssel zweimal in einer INSERT-Anweisung bricht die
    ganze Anweisung ab, also die ganze Datei.
    """
    unique = {}
    for row in rows:
        key = (row["metering_point_id"], row["direction"], row["measured_at"])
        unique[key] = row
    return list(unique.values())


def save_metering_point_readings(rows, source_document_id=None, batch_size=5000):
    """E66 Zeilen schreiben, unveränderte überspringen.

    Returns:
        dict mit written, new, corrected, unchanged und samples.
    """
    empty = {"written": 0, "new": 0, "corrected": 0, "unchanged": 0, "samples": []}
    rows = _dedupe(rows)
    if not rows:
        return empty

    try:
        from psycopg2.extras import execute_values

        with _get_connection() as conn:
            with conn.cursor() as cur:
                points = sorted({row["metering_point_id"] for row in rows})
                execute_values(cur, _POINT_STUB_SQL, [(point,) for point in points])

                values = [
                    tuple(
                        row.get(column)
                        if column != "source_document_id"
                        else row.get(column, source_document_id)
                        for column in _READING_COLUMNS
                    )
                    for row in rows
                ]
                changed = []
                for start in range(0, len(values), batch_size):
                    batch = values[start : start + batch_size]
                    returned = execute_values(
                        cur,
                        _READING_UPSERT_SQL,
                        batch,
                        page_size=1000,
                        fetch=True,
                    )
                    changed.extend(returned or [])

                new = sum(1 for row in changed if row["inserted"])
                corrected = len(changed) - new
                samples = [
                    (
                        row["metering_point_id"],
                        row["direction"],
                        row["measured_at"],
                    )
                    for row in changed
                    if not row["inserted"]
                ][:20]
                return {
                    "written": len(values),
                    "new": new,
                    "corrected": corrected,
                    "unchanged": len(values) - len(changed),
                    "samples": samples,
                }
    except Exception as e:
        logger.error(f"[DB] Error saving metering point readings: {e}")
        return empty


def upsert_metering_points(points):
    """Register anreichern, ohne bestehende Werte zu leeren."""
    points = [p for p in points if p.get("metering_point_id")]
    if not points:
        return 0
    try:
        from psycopg2.extras import execute_values

        with _get_connection() as conn:
            with conn.cursor() as cur:
                values = [
                    (
                        point["metering_point_id"],
                        point.get("vnb_community_id") or None,
                        point.get("community_id") or None,
                        point.get("building_id") or None,
                        point.get("alias") or None,
                        point.get("address") or None,
                    )
                    for point in points
                ]
                execute_values(cur, _POINT_UPSERT_SQL, values)
                return len(values)
    except Exception as e:
        logger.error(f"[DB] Error upserting metering points: {e}")
        return 0


def get_metering_points(community_id=None, active_only=True):
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                query = "SELECT * FROM metering_points WHERE 1 = 1"
                params = []
                if community_id:
                    query += " AND community_id = %s"
                    params.append(community_id)
                if active_only:
                    query += " AND active = TRUE"
                query += " ORDER BY metering_point_id"
                cur.execute(query, params)
                return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error getting metering points: {e}")
        return []


def get_metering_point(metering_point_id):
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM metering_points WHERE metering_point_id = %s",
                    (metering_point_id,),
                )
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        logger.error(f"[DB] Error getting metering point: {e}")
        return None


def get_metering_point_readings(
    metering_point_id, direction=None, start=None, end=None, limit=1000
):
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                query = (
                    "SELECT * FROM metering_point_readings WHERE metering_point_id = %s"
                )
                params = [metering_point_id]
                if direction:
                    query += " AND direction = %s"
                    params.append(direction)
                if start:
                    query += " AND measured_at >= %s"
                    params.append(start)
                if end:
                    query += " AND measured_at <= %s"
                    params.append(end)
                query += " ORDER BY measured_at DESC LIMIT %s"
                params.append(limit)
                cur.execute(query, params)
                return [_floatify(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error getting metering point readings: {e}")
        return []


def get_community_metering_points(community_id):
    """Alle aktiven Messpunkte einer LEG mit dem Mitgliedschaftsstatus.

    LEFT JOIN, nicht INNER: ein Messpunkt ohne Mitgliedschaftszeile muss
    zurückkommen, damit die Abrechnung ihn benennen kann statt ihn stillschweigend
    zu übergehen.
    """
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT mp.metering_point_id,
                           mp.building_id,
                           mp.alias,
                           cm.status AS member_status
                    FROM metering_points mp
                    LEFT JOIN community_members cm
                           ON cm.community_id = mp.community_id
                          AND cm.building_id = mp.building_id
                    WHERE mp.community_id = %s
                      AND mp.active = TRUE
                    ORDER BY mp.metering_point_id
                    """,
                    (community_id,),
                )
                return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error getting community metering points: {e}")
        return []


def get_unassigned_period_metering_point_ids(community_id, period_start, period_end):
    """Aktive, nicht zugeordnete Messpunkte finden, die zu dieser LEG gehören.

    Ein Messpunkt, der noch keiner OpenLEG Community zugeordnet ist
    (community_id IS NULL), aber im abgefragten Zeitraum Messwerte aus
    SDAT Importen mit denselben öffentlichen VNB LEG Identifikatoren
    (sdat_imports.vnb_community_id) liefert wie die bereits zugeordneten
    aktiven Messpunkte dieser Community, gehört fachlich zu dieser LEG und
    muss vor der Abrechnung zugeordnet werden.

    Die VNB Identifikatoren werden aus den aktiven, dieser Community
    zugeordneten Messpunkten und deren Messwerten im halboffenen Intervall
    [period_start, period_end) abgeleitet. Messpunkte anderer VNB LEGs
    bleiben unsichtbar.

    Ein Fehler wird nicht als leeres Ergebnis verschluckt: die Abrechnung
    muss geschlossen ausfallen statt eine Periode ohne die fehlenden
    Messpunkte zu verrechnen.

    Returns:
        Sortierte Liste eindeutiger metering_point_id.
    """
    with _get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT mp.metering_point_id
                FROM metering_points mp
                JOIN metering_point_readings r
                  ON r.metering_point_id = mp.metering_point_id
                 AND r.measured_at >= %s
                 AND r.measured_at < %s
                JOIN sdat_imports si
                  ON si.document_id = r.source_document_id
                WHERE mp.community_id IS NULL
                  AND mp.active = TRUE
                  AND si.vnb_community_id IN (
                      SELECT DISTINCT si2.vnb_community_id
                      FROM metering_points mp2
                      JOIN metering_point_readings r2
                        ON r2.metering_point_id = mp2.metering_point_id
                       AND r2.measured_at >= %s
                       AND r2.measured_at < %s
                      JOIN sdat_imports si2
                        ON si2.document_id = r2.source_document_id
                      WHERE mp2.community_id = %s
                        AND mp2.active = TRUE
                        AND si2.vnb_community_id IS NOT NULL
                  )
                ORDER BY mp.metering_point_id
                """,
                (period_start, period_end, period_start, period_end, community_id),
            )
            return [row["metering_point_id"] for row in cur.fetchall()]


def get_period_readings(community_id, period_start, period_end):
    """Messwerte einer LEG im halboffenen Intervall [period_start, period_end).

    Das Ende ist exklusiv. Ein inklusives Ende würde das Grenzintervall in zwei
    Perioden doppelt verrechnen.
    """
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT r.metering_point_id,
                           r.direction,
                           r.measured_at,
                           r.resolution_minutes,
                           r.total_kwh,
                           r.grid_kwh,
                           r.community_kwh,
                           r.source_document_id
                    FROM metering_point_readings r
                    JOIN metering_points mp
                      ON mp.metering_point_id = r.metering_point_id
                    WHERE mp.community_id = %s
                      AND r.measured_at >= %s
                      AND r.measured_at < %s
                    ORDER BY r.measured_at, r.metering_point_id, r.direction
                    """,
                    (community_id, period_start, period_end),
                )
                return [_floatify(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error getting period readings: {e}")
        return []


def get_metering_point_reading_stats(metering_point_id=None):
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                query = """
                    SELECT COUNT(*) as total_readings,
                           COUNT(DISTINCT metering_point_id) as total_points,
                           MIN(measured_at) as first_reading,
                           MAX(measured_at) as last_reading,
                           SUM(total_kwh) as total_kwh,
                           SUM(grid_kwh) as grid_kwh,
                           SUM(community_kwh) as community_kwh
                    FROM metering_point_readings
                """
                params = []
                if metering_point_id:
                    query += " WHERE metering_point_id = %s"
                    params.append(metering_point_id)
                cur.execute(query, params)
                row = cur.fetchone()
                return dict(row) if row else {}
    except Exception as e:
        logger.error(f"[DB] Error getting metering point stats: {e}")
        return {}


def record_sdat_import(document):
    """Eine importierte Datei im Ledger festhalten."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO sdat_imports (
                        document_id, doc_type, file_name, vnb_community_id,
                        document_created_at, period_start, period_end,
                        block_count, row_count, new_count, corrected_count
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (document_id) DO UPDATE SET
                        file_name = EXCLUDED.file_name,
                        block_count = EXCLUDED.block_count,
                        row_count = EXCLUDED.row_count,
                        new_count = EXCLUDED.new_count,
                        corrected_count = EXCLUDED.corrected_count,
                        imported_at = NOW()
                """,
                    (
                        document.get("document_id"),
                        document.get("doc_type"),
                        document.get("file_name"),
                        document.get("vnb_community_id"),
                        document.get("document_created_at"),
                        document.get("period_start"),
                        document.get("period_end"),
                        document.get("block_count", 0),
                        document.get("row_count", 0),
                        document.get("new_count", 0),
                        document.get("corrected_count", 0),
                    ),
                )
                return True
    except Exception as e:
        logger.error(f"[DB] Error recording SDAT import: {e}")
        return False


def get_sdat_import(document_id):
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM sdat_imports WHERE document_id = %s",
                    (document_id,),
                )
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        logger.error(f"[DB] Error getting SDAT import: {e}")
        return None


def get_sdat_import_index():
    """Das Ledger als Mengen laden: ein Query statt einer Abfrage pro Datei.

    Der Import vergleicht jede Datei im Verzeichnis gegen das Ledger. Bei einem
    Archiv aus einem Jahr Lieferungen sind das hunderte Abfragen pro Lauf, für
    eine Tabelle, die komplett in den Speicher passt.

    Bei einem Fehler kommen leere Mengen zurück. Der Import macht dann die volle
    Arbeit; er darf nie eine Datei überspringen, weil das Ledger unlesbar war.
    """
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT document_id, file_name FROM sdat_imports")
                rows = cur.fetchall()
                return {
                    "document_ids": frozenset(
                        row["document_id"] for row in rows if row["document_id"]
                    ),
                    "file_names": frozenset(
                        row["file_name"] for row in rows if row["file_name"]
                    ),
                }
    except Exception as e:
        logger.error(f"[DB] Error getting SDAT import index: {e}")
        return {"document_ids": frozenset(), "file_names": frozenset()}
