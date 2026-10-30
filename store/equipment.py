# SPDX-License-Identifier: AGPL-3.0-or-later
"""Equipment belongs to a verified building owner, independently of LEG membership."""

import uuid

from psycopg2.extras import Json

from equipment import MAX_RECORDS, EquipmentError, reset_model_evidence, validate_record


def _get_connection():
    import database

    return database.get_connection()


def create_tables(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS equipment_records (
            id UUID PRIMARY KEY,
            owner_building_id VARCHAR(64) NOT NULL
                REFERENCES buildings(building_id) ON DELETE CASCADE,
            record JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_equipment_owner
        ON equipment_records(owner_building_id)
    """)


def _owner(cur, actor):
    cur.execute(
        "SELECT verified FROM buildings WHERE building_id=%s FOR UPDATE", (actor,)
    )
    row = cur.fetchone()
    if not row or not row["verified"]:
        raise EquipmentError("verified_owner_required", 403)


def _record(row):
    return {"id": str(row["id"]), **row["record"]}


def create_equipment(actor, payload):
    record = validate_record(payload)
    with _get_connection() as conn, conn.cursor() as cur:
        _owner(cur, actor)
        cur.execute(
            "SELECT COUNT(*) AS count FROM equipment_records WHERE owner_building_id=%s",
            (actor,),
        )
        if cur.fetchone()["count"] >= MAX_RECORDS:
            raise EquipmentError("record_limit_reached", 409)
        cur.execute(
            "INSERT INTO equipment_records(id,owner_building_id,record) VALUES (%s,%s,%s) RETURNING id,record",
            (str(uuid.uuid4()), actor, Json(record)),
        )
        return _record(cur.fetchone())


def list_equipment(actor):
    with _get_connection() as conn, conn.cursor() as cur:
        _owner(cur, actor)
        cur.execute(
            "SELECT id,record FROM equipment_records WHERE owner_building_id=%s ORDER BY created_at,id",
            (actor,),
        )
        return [_record(row) for row in cur.fetchall()]


def get_equipment(actor, record_id):
    with _get_connection() as conn, conn.cursor() as cur:
        _owner(cur, actor)
        cur.execute(
            "SELECT id,record FROM equipment_records WHERE id=%s AND owner_building_id=%s",
            (record_id, actor),
        )
        row = cur.fetchone()
        if not row:
            raise EquipmentError("record_not_found", 404)
        return _record(row)


def update_equipment(actor, record_id, payload):
    record = validate_record(payload)
    with _get_connection() as conn, conn.cursor() as cur:
        _owner(cur, actor)
        cur.execute(
            "SELECT record FROM equipment_records WHERE id=%s AND owner_building_id=%s",
            (record_id, actor),
        )
        previous = cur.fetchone()
        if not previous:
            raise EquipmentError("record_not_found", 404)
        record = reset_model_evidence(previous["record"], record)
        cur.execute(
            "UPDATE equipment_records SET record=%s,updated_at=CURRENT_TIMESTAMP WHERE id=%s AND owner_building_id=%s RETURNING id,record",
            (Json(record), record_id, actor),
        )
        row = cur.fetchone()
        if not row:
            raise EquipmentError("record_not_found", 404)
        return _record(row)


def delete_equipment(actor, record_id):
    with _get_connection() as conn, conn.cursor() as cur:
        _owner(cur, actor)
        cur.execute(
            "DELETE FROM equipment_records WHERE id=%s AND owner_building_id=%s RETURNING id",
            (record_id, actor),
        )
        if not cur.fetchone():
            raise EquipmentError("record_not_found", 404)
        return {"deleted": True}
