# SPDX-License-Identifier: AGPL-3.0-or-later
"""Persistence for community operator credentials, events and deliveries."""

import secrets
import uuid

from psycopg2.extras import Json


def _get_connection():
    import database

    return database.get_connection()


def event_id_for(event_type, aggregate_id):
    """Return the stable identifier for one aggregate lifecycle transition."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"openleg:{event_type}:{aggregate_id}"))


def _subscription_capability(event_type):
    if event_type.startswith("metering."):
        return "metering.read"
    if event_type.startswith("invoice.case."):
        return "cases.read"
    if event_type.startswith("invoice."):
        return "billing.read"
    if event_type.startswith("payment."):
        return "payments.read"
    if event_type.startswith("formation."):
        return "formation.read"
    if event_type.startswith("membership."):
        return "membership.read"
    return None


def enqueue_event(
    cur, event_type, aggregate_id, community_id, payload, *, event_identity=None
):
    """Write an event and its deliveries through the caller's transaction."""
    event_id = event_id_for(event_type, event_identity or aggregate_id)
    cur.execute(
        """INSERT INTO operator_events
                  (event_id,event_type,schema_version,aggregate_id,community_id,payload)
           VALUES (%s,%s,'operator-event/1',%s,%s,%s)
           ON CONFLICT (event_id) DO NOTHING""",
        (event_id, event_type, aggregate_id, community_id, Json(payload)),
    )
    capability = _subscription_capability(event_type)
    cur.execute(
        """INSERT INTO operator_webhook_deliveries (delivery_id,event_id,client_id)
           SELECT gen_random_uuid()::text,%s,id FROM operator_api_clients
           WHERE community_id=%s AND active=TRUE AND webhook_url IS NOT NULL
             AND (%s IS NULL OR capabilities ? %s)
           ON CONFLICT (event_id,client_id) DO NOTHING""",
        (event_id, community_id, capability, capability),
    )
    return event_id


def create_client(
    community_id,
    created_by,
    name,
    capabilities,
    token_hash,
    webhook_url,
    rate_limit_per_hour=100,
):
    client_id = str(uuid.uuid4())
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO operator_api_clients (id,community_id,created_by,name,capabilities,token_hash,webhook_url,rate_limit_per_hour,webhook_secret)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (
                client_id,
                community_id,
                created_by,
                name,
                Json(capabilities),
                token_hash,
                webhook_url,
                rate_limit_per_hour,
                "olwhsec_" + secrets.token_urlsafe(32),
            ),
        )
        return dict(cur.fetchone())


def get_client_by_token_hash(token_hash):
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM operator_api_clients WHERE token_hash=%s AND active=TRUE",
            (token_hash,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def list_clients(community_id):
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM operator_api_clients WHERE community_id=%s ORDER BY created_at DESC",
            (community_id,),
        )
        return [dict(row) for row in cur.fetchall()]


def rotate_client(community_id, client_id, token_hash):
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE operator_api_clients SET token_hash=%s,webhook_secret=%s,active=TRUE,webhook_secret_version=webhook_secret_version+1,rotated_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=%s AND community_id=%s RETURNING *",
            (
                token_hash,
                "olwhsec_" + secrets.token_urlsafe(32),
                client_id,
                community_id,
            ),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def revoke_client(community_id, client_id):
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE operator_api_clients SET active=FALSE,revoked_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=%s AND community_id=%s RETURNING *",
            (client_id, community_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def usage_count(client_id):
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS count FROM operator_api_usage WHERE client_id=%s AND called_at>CURRENT_TIMESTAMP-INTERVAL '1 hour'",
            (client_id,),
        )
        return cur.fetchone()["count"]


def claim_usage(client_id, endpoint, limit):
    """Atomically consume one request slot in the client's hourly window."""
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (client_id,))
        cur.execute(
            "SELECT COUNT(*) AS count FROM operator_api_usage WHERE client_id=%s AND called_at>CURRENT_TIMESTAMP-INTERVAL '1 hour'",
            (client_id,),
        )
        if cur.fetchone()["count"] >= limit:
            return False
        cur.execute(
            "INSERT INTO operator_api_usage (client_id,endpoint) VALUES (%s,%s)",
            (client_id, endpoint),
        )
        cur.execute(
            "UPDATE operator_api_clients SET last_used_at=CURRENT_TIMESTAMP WHERE id=%s",
            (client_id,),
        )
        return True


def record_usage(client_id, endpoint):
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO operator_api_usage (client_id,endpoint) VALUES (%s,%s)",
            (client_id, endpoint),
        )
        cur.execute(
            "UPDATE operator_api_clients SET last_used_at=CURRENT_TIMESTAMP WHERE id=%s",
            (client_id,),
        )


def create_event(event_type, aggregate_id, community_id, payload):
    with _get_connection() as conn, conn.cursor() as cur:
        event_id = enqueue_event(cur, event_type, aggregate_id, community_id, payload)
        return {
            "event_id": event_id,
            "event_type": event_type,
            "aggregate_id": aggregate_id,
            "community_id": community_id,
            "payload": payload,
            "schema_version": "operator-event/1",
        }


def get_pending_deliveries(max_attempts=5, limit=100, *, community_id=None):
    """Atomically claim a bounded delivery batch for one worker."""
    safe_limit = max(1, min(int(limit), 100))
    scope = " AND e.community_id=%s" if community_id is not None else ""
    parameters = (max_attempts,)
    if community_id is not None:
        parameters += (community_id,)
    parameters += (safe_limit,)
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"""WITH candidates AS (
                   SELECT d.delivery_id
                     FROM operator_webhook_deliveries d
                     JOIN operator_events e ON e.event_id=d.event_id
                     JOIN operator_api_clients c ON c.id=d.client_id
                    WHERE (d.status IN ('pending','retry') OR
                           (d.status='processing' AND d.claimed_at < CURRENT_TIMESTAMP - INTERVAL '15 minutes'))
                      AND d.attempt_count<%s AND c.active=TRUE
                      AND d.next_attempt_at<=CURRENT_TIMESTAMP
                      AND e.schema_version='operator-event/1'
                      {scope}
                    ORDER BY e.occurred_at LIMIT %s FOR UPDATE OF d SKIP LOCKED
               ), claimed AS (
                   UPDATE operator_webhook_deliveries d
                      SET status='processing',claimed_at=CURRENT_TIMESTAMP,
                          claim_id=gen_random_uuid()::text
                     FROM candidates x WHERE x.delivery_id=d.delivery_id
                   RETURNING d.*
               )
               SELECT claimed.delivery_id,claimed.client_id,claimed.status,
                      claimed.claim_id,claimed.attempt_count,e.*,c.webhook_url,
                      c.webhook_secret_version,c.webhook_secret
               FROM claimed JOIN operator_events e ON e.event_id=claimed.event_id
               JOIN operator_api_clients c ON c.id=claimed.client_id
               ORDER BY e.occurred_at""",
            parameters,
        )
        return [dict(row) for row in cur.fetchall()]


def delivery_context(community_id, delivery_id):
    """Read the scoped recipient's identity and current signature version."""
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT d.client_id,d.attempt_count,c.webhook_secret_version,c.webhook_secret
               FROM operator_webhook_deliveries d
               JOIN operator_api_clients c ON c.id=d.client_id
               JOIN operator_events e ON e.event_id=d.event_id
               WHERE d.delivery_id=%s AND e.community_id=%s AND c.community_id=%s""",
            (delivery_id, community_id, community_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def record_attempt(delivery_id, *, claim_id, status, response_status, retryable):
    if status not in {"retry", "delivered", "failed"}:
        raise ValueError("Invalid completed webhook delivery status")
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE operator_webhook_deliveries SET status=%s,response_status=%s,attempt_count=attempt_count+1,last_attempt_at=CURRENT_TIMESTAMP,
            next_attempt_at=CASE WHEN %s THEN CURRENT_TIMESTAMP + make_interval(secs => LEAST(3600, POWER(2, attempt_count)::int * 30)) ELSE next_attempt_at END,
            claim_id=NULL WHERE delivery_id=%s AND status='processing' AND claim_id=%s""",
            (status, response_status, retryable, delivery_id, claim_id),
        )


def list_deliveries(community_id, limit=100):
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT d.delivery_id,d.status,d.attempt_count,d.response_status,
                      d.next_attempt_at,d.last_attempt_at,e.event_id,e.event_type,
                      e.aggregate_id,e.occurred_at
               FROM operator_webhook_deliveries d
               JOIN operator_events e ON e.event_id=d.event_id
               WHERE e.community_id=%s ORDER BY e.occurred_at DESC LIMIT %s""",
            (community_id, max(1, min(int(limit), 100))),
        )
        return [dict(row) for row in cur.fetchall()]


def list_events(community_id, limit=100, *, event_types=None):
    """Filter scoped event types before bounding the most recent audit identities."""
    selected_types = list(event_types) if event_types is not None else None
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT event_id,event_type,aggregate_id,occurred_at
               FROM operator_events WHERE community_id=%s
                 AND (%s::text[] IS NULL OR event_type=ANY(%s))
               ORDER BY occurred_at DESC,event_id LIMIT %s""",
            (
                community_id,
                selected_types,
                selected_types,
                max(1, min(int(limit), 100)),
            ),
        )
        return [dict(row) for row in cur.fetchall()]


def retry_delivery(community_id, delivery_id):
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE operator_webhook_deliveries d SET status='retry', claim_id=NULL,
                      next_attempt_at=CURRENT_TIMESTAMP
               FROM operator_events e WHERE e.event_id=d.event_id
                 AND e.community_id=%s AND d.delivery_id=%s AND d.status='failed'
               RETURNING d.delivery_id,d.status,d.attempt_count""",
            (community_id, delivery_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None
