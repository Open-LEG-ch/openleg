# SPDX-License-Identifier: AGPL-3.0-or-later
"""Community-scoped operational read models and atomic API actions."""

import hashlib
import json
from datetime import date

import billing_lifecycle
import invoice_queries
from store.operator_api import enqueue_event


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


def respond_case(case_id, community_id, actor_id, message, status, key):
    action = f"case.respond:{case_id}"
    fingerprint = request_hash({"message": message, "status": status})
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"{community_id}:{action}:{key}",),
        )
        replay = _replay(cur, community_id, action, key, fingerprint)
        if replay:
            return replay
        cur.execute(
            "SELECT status FROM invoice_queries WHERE id=%s AND community_id=%s FOR UPDATE",
            (case_id, community_id),
        )
        row = cur.fetchone()
        if not row:
            return None
        clean_message = invoice_queries.validate_message(message)
        invoice_queries.require_transition(row["status"], status)
        cur.execute(
            "INSERT INTO invoice_query_messages(query_id,actor_id,message) VALUES (%s,%s,%s)",
            (case_id, actor_id, clean_message),
        )
        cur.execute(
            "UPDATE invoice_queries SET status=%s,updated_at=NOW() WHERE id=%s",
            (status, case_id),
        )
        cur.execute(
            "INSERT INTO invoice_query_events(query_id,actor_id,previous_status,new_status) VALUES (%s,%s,%s,%s) RETURNING id",
            (case_id, actor_id, row["status"], status),
        )
        transition_id = cur.fetchone()["id"]
        event_id = enqueue_event(
            cur,
            "invoice.case.updated",
            str(transition_id),
            community_id,
            {"status": status},
        )
        response = {
            "id": case_id,
            "status": status,
            "event_id": event_id,
            "replayed": False,
        }
        _remember(cur, community_id, action, key, fingerprint, response)
        return response


def confirm_payment(entry_id, invoice_id, community_id, actor_id, key):
    action = f"payment.confirm:{entry_id}"
    fingerprint = request_hash({"invoice_id": invoice_id})
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"{community_id}:{action}:{key}",),
        )
        replay = _replay(cur, community_id, action, key, fingerprint)
        if replay:
            return replay
        cur.execute(
            "SELECT * FROM bank_statement_entries WHERE id=%s AND community_id=%s FOR UPDATE",
            (entry_id, community_id),
        )
        entry = cur.fetchone()
        if not entry or entry["match_decision"] not in {
            "unmatched",
            "ambiguous",
            "mismatch",
        }:
            return None
        cur.execute(
            """SELECT i.id,i.gross_chf,COALESCE((SELECT e.new_state FROM invoice_lifecycle_events e WHERE e.invoice_id=i.id ORDER BY e.id DESC LIMIT 1),'issued') lifecycle_state FROM invoices i WHERE i.id=%s AND i.community_id=%s FOR UPDATE""",
            (invoice_id, community_id),
        )
        invoice = cur.fetchone()
        if (
            not invoice
            or str(entry["currency"]) != "CHF"
            or entry["is_reversal"]
            or entry["amount"] != invoice["gross_chf"]
        ):
            raise billing_lifecycle.InvoiceLifecycleError(
                "Die Zahlung passt nicht exakt zur Rechnung."
            )
        billing_lifecycle.next_state(
            invoice["lifecycle_state"],
            "pay",
            reference=entry["payment_reference"],
            effective_date=entry["booking_date"]
            if isinstance(entry["booking_date"], date)
            else date.fromisoformat(str(entry["booking_date"])),
        )
        cur.execute(
            "UPDATE bank_statement_entries SET invoice_id=%s,match_decision='matched' WHERE id=%s",
            (invoice_id, entry_id),
        )
        cur.execute(
            """INSERT INTO invoice_lifecycle_events(invoice_id,community_id,actor_id,event_type,previous_state,new_state,reference,effective_date,idempotency_key) VALUES (%s,%s,%s,'paid','delivered','paid',%s,%s,%s) RETURNING id""",
            (
                invoice_id,
                community_id,
                actor_id,
                entry["payment_reference"],
                entry["booking_date"],
                f"operator:{key}",
            ),
        )
        transition_id = cur.fetchone()["id"]
        event_id = enqueue_event(
            cur,
            "payment.match.confirmed",
            str(transition_id),
            community_id,
            {"status": "matched"},
        )
        response = {
            "id": entry_id,
            "match_decision": "matched",
            "event_id": event_id,
            "replayed": False,
        }
        _remember(cur, community_id, action, key, fingerprint, response)
        return response
