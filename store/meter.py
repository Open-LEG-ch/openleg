# SPDX-License-Identifier: AGPL-3.0-or-later
"""Smart-meter CSV persistence repository.

Resolves the connection seam via ``database.get_connection`` at call time so
monkeypatches keep working and ``database`` can re-export these functions.
"""

import logging

logger = logging.getLogger(__name__)


def _get_connection():
    import database

    return database.get_connection()


def save_meter_readings(building_id, readings, source="csv"):
    """Bulk insert meter readings. readings = list of (timestamp, consumption, production, feed_in)."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                from psycopg2.extras import execute_values

                values = [
                    (building_id, r[0], r[1], r[2], r[3], source) for r in readings
                ]
                execute_values(
                    cur,
                    """
                    INSERT INTO meter_readings (building_id, timestamp, consumption_kwh, production_kwh, feed_in_kwh, source)
                    VALUES %s
                    ON CONFLICT (building_id, timestamp) DO UPDATE SET
                        consumption_kwh = EXCLUDED.consumption_kwh,
                        production_kwh = EXCLUDED.production_kwh,
                        feed_in_kwh = EXCLUDED.feed_in_kwh
                """,
                    values,
                )
                return len(values)
    except Exception as e:
        logger.error(f"[DB] Error saving meter readings: {e}")
        return 0


def get_meter_readings(building_id, start=None, end=None, limit=1000):
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            query = "SELECT * FROM meter_readings WHERE building_id = %s"
            params = [building_id]
            if start:
                query += " AND timestamp >= %s"
                params.append(start)
            if end:
                query += " AND timestamp <= %s"
                params.append(end)
            query += " ORDER BY timestamp DESC LIMIT %s"
            params.append(limit)
            cur.execute(query, params)
            return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error getting meter readings: {e}")
        return []


def get_meter_reading_stats(building_id):
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT COUNT(*) as total_readings,
                           MIN(timestamp) as first_reading,
                           MAX(timestamp) as last_reading,
                           SUM(consumption_kwh) as total_consumption,
                           SUM(production_kwh) as total_production,
                           SUM(feed_in_kwh) as total_feed_in
                    FROM meter_readings WHERE building_id = %s
                """,
                (building_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else {}
    except Exception as e:
        logger.error(f"[DB] Error getting meter stats: {e}")
        return {}
