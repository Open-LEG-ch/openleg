# SPDX-License-Identifier: AGPL-3.0-or-later
"""Community-scoped operational read models and atomic API actions."""

import hashlib
import json


def _get_connection():
    import database

    return database.get_connection()


def _page(query, params, *, limit, cursor):
    bounded = max(1, min(int(limit), 100))
    after = int(cursor or 0)
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(query, (*params, after, bounded + 1))
        rows = [dict(row) for row in cur.fetchall()]
    next_cursor = str(rows[bounded - 1]["id"]) if len(rows) > bounded else None
    return rows[:bounded], next_cursor


def list_metering_jobs(community_id, *, status=None, limit=50, cursor=None):
    return _page(
        """SELECT r.id,r.territory,r.started_at,r.finished_at,r.status,r.attempts,
                  r.downloaded_files,r.imported_files,r.imported_readings,r.error_code
             FROM sdat_ingestion_runs r JOIN communities c ON c.community_id=%s
            JOIN buildings b ON b.building_id=c.admin_building_id
            WHERE r.territory=b.city_id AND (%s IS NULL OR r.status=%s) AND r.id>%s
              AND 1=(SELECT COUNT(*) FROM communities scoped
                       JOIN buildings owner ON owner.building_id=scoped.admin_building_id
                      WHERE owner.city_id=r.territory)
            ORDER BY r.id LIMIT %s""",
        (community_id, status, status),
        limit=limit,
        cursor=cursor,
    )


def get_ingestion_retry(community_id, job_id):
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT s.* FROM sdat_ingestion_runs r
                 JOIN sdat_ingestion_schedules s ON s.territory=r.territory
                 JOIN communities c ON c.community_id=%s
                 JOIN buildings b ON b.building_id=c.admin_building_id
                WHERE r.id=%s AND r.status='failure' AND r.territory=b.city_id
                  AND 1=(SELECT COUNT(*) FROM communities scoped
                           JOIN buildings owner ON owner.building_id=scoped.admin_building_id
                          WHERE owner.city_id=r.territory)""",
            (community_id, job_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def request_hash(payload):
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def claim_ingestion_retry(community_id, job_id, key):
    """Reserve an eligible retry or replay its durable completed response."""
    action = f"metering.retry:{job_id}"
    fingerprint = request_hash({"job_id": job_id})
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """DELETE FROM operator_action_idempotency
                WHERE community_id=%s AND action=%s AND idempotency_key=%s
                  AND response IS NULL AND created_at<NOW()-INTERVAL '15 minutes'""",
            (community_id, action, key),
        )
        cur.execute(
            """INSERT INTO operator_action_idempotency(community_id,action,idempotency_key,request_hash,response)
               VALUES (%s,%s,%s,%s,NULL) ON CONFLICT DO NOTHING RETURNING idempotency_key""",
            (community_id, action, key, fingerprint),
        )
        claimed = cur.fetchone()
        if not claimed:
            replay = _replay(cur, community_id, action, key, fingerprint)
            return {"replay": replay} if replay else {"pending": True}
        schedule = get_ingestion_retry_in_cursor(cur, community_id, job_id)
        if not schedule:
            cur.execute(
                "DELETE FROM operator_action_idempotency WHERE community_id=%s AND action=%s AND idempotency_key=%s",
                (community_id, action, key),
            )
            return None
        return {"schedule": schedule}


def get_ingestion_retry_in_cursor(cur, community_id, job_id):
    cur.execute(
        """SELECT s.* FROM sdat_ingestion_runs r
             JOIN sdat_ingestion_schedules s ON s.territory=r.territory
             JOIN communities c ON c.community_id=%s
             JOIN buildings b ON b.building_id=c.admin_building_id
            WHERE r.id=%s AND r.status='failure' AND r.territory=b.city_id
              AND 1=(SELECT COUNT(*) FROM communities scoped
                       JOIN buildings owner ON owner.building_id=scoped.admin_building_id
                      WHERE owner.city_id=r.territory)""",
        (community_id, job_id),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def complete_ingestion_retry(community_id, job_id, key, response):
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE operator_action_idempotency SET response=%s::jsonb
                WHERE community_id=%s AND action=%s AND idempotency_key=%s AND response IS NULL""",
            (
                json.dumps(response, default=str),
                community_id,
                f"metering.retry:{job_id}",
                key,
            ),
        )


def release_ingestion_retry(community_id, job_id, key):
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """DELETE FROM operator_action_idempotency
                WHERE community_id=%s AND action=%s AND idempotency_key=%s
                  AND response IS NULL""",
            (community_id, f"metering.retry:{job_id}", key),
        )


def list_calculated_deliveries(community_id, *, status=None, limit=50, cursor=None):
    return _page(
        """SELECT id,contract_version,format_version,transport,community_id,
                  period_start,period_end,status,diagnostics,record_count,received_at
             FROM vnb_calculated_values_deliveries
            WHERE community_id=%s AND (%s IS NULL OR status=%s) AND id>%s
            ORDER BY id LIMIT %s""",
        (community_id, status, status),
        limit=limit,
        cursor=cursor,
    )


def list_periods(community_id, *, status=None, limit=50, cursor=None):
    return _page(
        """SELECT id,community_id,period_start,period_end,total_production_kwh,
                  total_allocated_kwh,total_surplus_kwh,total_network_discount_chf,status
             FROM billing_periods WHERE community_id=%s AND (%s IS NULL OR status=%s)
                  AND id>%s ORDER BY id LIMIT %s""",
        (community_id, status, status),
        limit=limit,
        cursor=cursor,
    )


def list_invoices(community_id, *, status=None, limit=50, cursor=None):
    return _page(
        """SELECT i.id,i.invoice_number,i.gross_chf,i.issue_date,i.due_date,
                  COALESCE((SELECT e.new_state FROM invoice_lifecycle_events e
                   WHERE e.invoice_id=i.id ORDER BY e.id DESC LIMIT 1),'issued') lifecycle_state
             FROM invoices i WHERE i.community_id=%s AND
                  (%s IS NULL OR COALESCE((SELECT e.new_state FROM invoice_lifecycle_events e
                   WHERE e.invoice_id=i.id ORDER BY e.id DESC LIMIT 1),'issued')=%s)
                  AND i.id>%s ORDER BY i.id LIMIT %s""",
        (community_id, status, status),
        limit=limit,
        cursor=cursor,
    )


def list_cases(community_id, *, status=None, limit=50, cursor=None):
    return _page(
        """SELECT id,invoice_id,category,status,created_at,updated_at
             FROM invoice_queries WHERE community_id=%s AND (%s IS NULL OR status=%s)
                  AND id>%s ORDER BY id LIMIT %s""",
        (community_id, status, status),
        limit=limit,
        cursor=cursor,
    )


def list_payments(community_id, *, status=None, limit=50, cursor=None):
    return _page(
        """SELECT id,invoice_id,entry_reference,booking_date,amount,currency,
                  payment_reference,is_reversal,match_decision
             FROM bank_statement_entries WHERE community_id=%s
                  AND (%s IS NULL OR match_decision=%s) AND id>%s ORDER BY id LIMIT %s""",
        (community_id, status, status),
        limit=limit,
        cursor=cursor,
    )


def _replay(cur, community_id, action, key, fingerprint):
    cur.execute(
        "SELECT response,request_hash FROM operator_action_idempotency WHERE community_id=%s AND action=%s AND idempotency_key=%s",
        (community_id, action, key),
    )
    row = cur.fetchone()
    if not row:
        return None
    if row["request_hash"] != fingerprint:
        raise ValueError("Idempotency-Key was already used for another request")
    response = row["response"]
    if response is None:
        return None
    if isinstance(response, str):
        response = json.loads(response)
    return {**response, "replayed": True}


def _remember(cur, community_id, action, key, fingerprint, response):
    cur.execute(
        "INSERT INTO operator_action_idempotency(community_id,action,idempotency_key,request_hash,response) VALUES (%s,%s,%s,%s,%s::jsonb)",
        (community_id, action, key, fingerprint, json.dumps(response, default=str)),
    )
